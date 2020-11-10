import json
import os
import boto3
import botocore.exceptions
from boto3.dynamodb.conditions import Attr 
from netaddr import *
from datetime import datetime

dynamodb = boto3.resource('dynamodb')
dynamodbTableName = os.environ['DYNAMODB_TABLE_NAME']
table = dynamodb.Table(dynamodbTableName)
supernetTable = dynamodb.Table(os.environ['SUPERNETSTABLE'])

def returnSupernet(Table, Region, Env):
    supernets = []
    Records = scanDDB(Table, Region=Region, Env=Env)
    for Record in Records['Items']:
        supernets.append(Record['Cidr'])
        while 'LastEvaluatedKey' in Records:
            Records = scanDDB(Table, LastEvaluatedKey=Records['LastEvaluatedKey'], Region=Region, Env=Env)
            for Record in Records['Items']:
                supernets.append(Record['Cidr'])
    if len(supernets) > 0:
        return supernets
    else:
        raise LookupError('no supernets where found for this region or environment')

def addToDDB(cidr, AccountId, Requestor, Reason, Region, Env, ProjectCode, StackId):
    response = table.put_item(
    Item={
        'Cidr': cidr,
        'AccountId': AccountId,
        'Requestor': Requestor,
        'ProjectCode': ProjectCode,
        'Reason': Reason,
        'Region': Region,
        'Env': Env,
        'StackId': StackId,
        'DateTime': datetime.now().strftime("%Y-%m-%d:%H:%M:%S")
        },
        ConditionExpression='attribute_not_exists(Cidr)'
    )
    
def deleteCidrDDB(cidr=None, stackId=None):
    if cidr == None:
        cidr = table.scan(ConsistentRead=True, FilterExpression=Attr("StackId").eq(stackId))['Items'][0]['Cidr']
    table.delete_item(
        Key={
            'Cidr': cidr
        }
    )

def getCidrDDB(cidr):
    cidrDetails = table.get_item(
        Key={
            'Cidr': cidr
        }
    )
    cidrDetails['Item']['AccountId'] = str(cidrDetails['Item']['AccountId'])
    return json.dumps(cidrDetails['Item'])

def updateCidrDDB(cidr, VpcId):
    response = table.update_item(
        Key={
            'Cidr': cidr
        },
        UpdateExpression="set VpcId=:v",
        ExpressionAttributeValues={
            ':v': VpcId
        },
        ReturnValues="UPDATED_NEW"
    )
    return response

def findAvailableSubnets(supernetSet, usedSubnets):
    for subnet in usedSubnets:
        supernetSet.remove(subnet)
    return supernetSet

def returnAvailableSubnet(supernetSet, usedSubnets, subnetPrefix):
    findAvailableSubnets(supernetSet, usedSubnets)
    freeSubnets = []
    for network in supernetSet.iter_cidrs():
        newNetwork = IPNetwork(network)
        subnets = list(newNetwork.subnet(subnetPrefix))
        for subnet in subnets:
            freeSubnets.append(subnet)
    if len(freeSubnets) > 0:
        return freeSubnets[0]
    else:
        raise LookupError('no available subets for this region and environment')
    
def scanDDB(Table, LastEvaluatedKey=None, Region=None, Env=None):
    if LastEvaluatedKey == None:
        Records = Table.scan(ConsistentRead=True, FilterExpression=Attr("Region").eq(Region) & Attr("Env").eq(Env))
        return Records
    elif LastEvaluatedKey != None:
        Records = Table.scan(ConsistentRead=True, ExclusiveStartKey=LastEvaluatedKey, FilterExpression=Attr("Region").eq(Region) & Attr("Env").eq(Env))
        return Records

def getUsedCidrs(Region, Env):
    usedSubnets = []
    Records = scanDDB(table, Region=Region, Env=Env)
    for Record in Records['Items']:
        usedSubnets.append(Record['Cidr'])
        while 'LastEvaluatedKey' in Records:
            Records = scanDDB(table, LastEvaluatedKey=Records['LastEvaluatedKey'], Region=Region, Env=Env)
            for Record in Records['Items']:
                usedSubnets.append(Record['Cidr'])
    return usedSubnets

def lambda_handler(event, context):
    print(event)
    if event['httpMethod'] == 'POST':
        for failureCount in range(0,3):
            if failureCount < 3:
                try:
                    prefix = int(event['queryStringParameters']['prefix'])
                    AccountId = int(event['queryStringParameters']['AccountId'])
                    Requestor = event['queryStringParameters']['Requestor']
                    Reason = event['queryStringParameters']['Reason']
                    Region = event['queryStringParameters']['Region']
                    Env = event['queryStringParameters']['Env']
                    if "StackId" in event['queryStringParameters']:
                        StackId = event['queryStringParameters']['StackId']
                    else:
                        StackId = None
                    
                    if "ProjectCode" in event['queryStringParameters']:
                        ProjectCode = event['queryStringParameters']['ProjectCode']
                    else:
                        ProjectCode = None
                    
                    supernet = returnSupernet(supernetTable, Region, Env)
                    usedSubnets = getUsedCidrs(Region, Env)
                
                    supernetSet = IPSet(supernet)
                    cidrToAssign = str(returnAvailableSubnet(supernetSet, usedSubnets, prefix))
                    
                    addToDDB(cidrToAssign, AccountId, Requestor, Reason, Region, Env, ProjectCode, StackId)
                    
                    response = {
                              'isBase64Encoded': False,
                              'statusCode': 200,
                              'headers': {},
                              'multiValueHeaders': {},
                              'body': '{"cidr": "' + cidrToAssign +'" }'
                            }
                    return response
                except botocore.exceptions.ClientError as e:
                    if e.response['Error']['Code'] == 'ConditionalCheckFailedException':
                        print(e)
                        print('record already allocated, retrying...')
                        continue
                except Exception as e:
                    response = {
                      'isBase64Encoded': False,
                      'statusCode': 500,
                      'headers': {},
                      'multiValueHeaders': {},
                      'body': '{"Error": "' + str(e) +'" }'
                    }
                    return response
                break
    elif event['httpMethod'] == 'DELETE':
        if "StackId" in event['queryStringParameters']:
            StackId = event['queryStringParameters']['StackId']
        else:
            StackId = None
        try:
            if "Cidr" in event['queryStringParameters']:
                cidr = event['queryStringParameters']['Cidr']
                deleteCidrDDB(cidr=cidr)
                response = {
                  'isBase64Encoded': False,
                  'statusCode': 200,
                  'headers': {},
                  'multiValueHeaders': {},
                  'body': 'CIDR: ' + cidr + ' deleted'
                }
                return response
            else:
                deleteCidrDDB(stackId=StackId)
                response = {
                  'isBase64Encoded': False,
                  'statusCode': 200,
                  'headers': {},
                  'multiValueHeaders': {},
                  'body': 'StackId: ' + StackId + ' deleted'
                }
                return response
        except Exception as e:
            response = {
              'isBase64Encoded': False,
              'statusCode': 500,
              'headers': {},
              'multiValueHeaders': {},
              'body': 'Error: ' + str(e)
            }
            return response
    elif event['httpMethod'] == 'PUT':
        try:
            cidr = event['queryStringParameters']['Cidr']
            VpcId = event['queryStringParameters']['VpcId']
            updateCidrDDB(cidr, VpcId)
            response = {
              'isBase64Encoded': False,
              'statusCode': 200,
              'headers': {},
              'multiValueHeaders': {},
              'body': 'CIDR: ' + cidr + ' updated'
            }
            return response
        except Exception as e:
            response = {
              'isBase64Encoded': False,
              'statusCode': 500,
              'headers': {},
              'multiValueHeaders': {},
              'body': 'Error: ' + str(e)
            }
            return response
    elif event['httpMethod'] == 'GET':
        try:
            cidr = event['queryStringParameters']['Cidr']
            cidrDetails = getCidrDDB(cidr)
            response = {
              'isBase64Encoded': False,
              'statusCode': 200,
              'headers': {},
              'multiValueHeaders': {},
              'body': 'CIDR information: ' + cidrDetails
            }
            return response
        except Exception as e:
            response = {
              'isBase64Encoded': False,
              'statusCode': 500,
              'headers': {},
              'multiValueHeaders': {},
              'body': 'Error: ' + str(e)
            }
            return response
