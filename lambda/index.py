import boto3
import os
import cfnresponse
from botocore.exceptions import ClientError

access_analyser_master_account=os.environ['ACCESS_ANALYSER_MASTER_ACCOUNT']
role_to_assume=os.environ['ROLE_TO_ASSUME']

org_client=boto3.client('organizations')
cloudtrail_client=boto3.client('cloudtrail')

def lambda_handler(event, context):
    control_tower_regions=get_control_tower_regions()
    accounts=get_all_accounts()
    ct_home_region=cloudtrail_client.describe_trails(
        trailNameList=[
            'aws-controltower-BaselineCloudTrail',
        ]
    )['trailList'][0]['HomeRegion']
    if 'RequestType' in event:    
        if (event['RequestType'] == 'Create' or event['RequestType'] == 'Update'):
            try:
                access_analyser_delegated_admin=org_client.list_delegated_administrators(
                    ServicePrincipal='access-analyzer.amazonaws.com'
                )
                if access_analyser_delegated_admin['DelegatedAdministrators']:
                    print(f"Delegated Administration has already been configured for Access Analyser to Account ID: {access_analyser_delegated_admin['DelegatedAdministrators'][0]['Id']}.")
                else:
                    try:
                        org_client.register_delegated_administrator(
                            AccountId=access_analyser_master_account,
                            ServicePrincipal='access-analyzer.amazonaws.com'
                        )
                        print(f"Delegated Administration for Access Analyzer is now configured to Account ID {access_analyser_master_account}.")
                    except ClientError as error:
                        print(error)
                access_analyser_master_account_session=assume_role(access_analyser_master_account, role_to_assume)

                for region in control_tower_regions:
                    access_analyser_client=access_analyser_master_account_session.client('accessanalyzer', region_name=region)
                    try:
                        response=access_analyser_client.create_analyzer(
                            analyzerName=f"Organization-Zone-of-Trust-{region}",
                            type='ORGANIZATION'
                        )
                        print(f"Successfully configured an Access Analyzer with an Organization Zone of Trust for Account ID {access_analyser_master_account} in Region {region}.")
                    except ClientError as error:
                        print(error)
                for account in accounts:
                    member_session=assume_role(account['Id'], role_to_assume)
                    member_client=member_session.client('accessanalyzer', region_name=ct_home_region)
                    try:
                        member_client.create_analyzer(
                            analyzerName=f"Account-Zone-of-Trust-{account['Id']}",
                            type='ACCOUNT'
                        )
                        print(f"Successfully configured an Access Analyzer with an Account Zone of Trust for Account ID {account['Id']} in Region {region}.")
                    except ClientError as error:
                        print(error)
                cfnresponse.send(event, context, cfnresponse.SUCCESS, {})
            except ClientError as error:
                print(error) 
                cfnresponse.send(event, context, cfnresponse.FAILED, error)  
        elif (event['RequestType'] == 'Delete'):
            try:
                access_analyser_master_account_session=assume_role(access_analyser_master_account, role_to_assume)
                for region in control_tower_regions:
                    access_analyser_client=access_analyser_master_account_session.client('accessanalyzer', region_name=region)
                    try:
                        response=access_analyser_client.delete_analyzer(
                            analyzerName=f"Organization-Zone-of-Trust-{region}"
                        )
                        print(f"Successfully deleted an Access Analyzer with an Organization Zone of Trust for Account ID {access_analyser_master_account} in Region {region}.")
                    except ClientError as error:
                        print(error)
                for account in accounts:
                    member_session=assume_role(account['Id'], role_to_assume)
                    member_client=member_session.client('accessanalyzer', region_name=ct_home_region)
                    try:
                        member_client.delete_analyzer(
                            analyzerName=f"Account-Zone-of-Trust-{account['Id']}"
                        )
                        print(f"Successfully deleted an Access Analyzer with an Account Zone of Trust for Account ID {account['Id']} in Region {region}.")
                    except ClientError as error:
                        print(error)
                org_client.deregister_delegated_administrator(
                    AccountId=access_analyser_master_account,
                    ServicePrincipal='access-analyzer.amazonaws.com'
                )
                print(f"Delegated Administration for Access Analyzer is now disabled.")
                cfnresponse.send(event, context, cfnresponse.SUCCESS, {})
            except ClientError as error:
                print(error) 
                cfnresponse.send(event, context, cfnresponse.FAILED, error)

def assume_role(aws_account_id, role_to_assume):
    sts_client=boto3.client('sts')
    response=sts_client.assume_role(
        RoleArn=f'arn:aws:iam::{aws_account_id}:role/{role_to_assume}',
        RoleSessionName='EnableSecurityHub'
    )
    sts_session=boto3.Session(
        aws_access_key_id=response['Credentials']['AccessKeyId'],
        aws_secret_access_key=response['Credentials']['SecretAccessKey'],
        aws_session_token=response['Credentials']['SessionToken']
    )
    print(f"Assumed session for Account ID: {aws_account_id}.")
    return sts_session

def get_control_tower_regions():
    cloudformation_client=boto3.client('cloudformation')
    control_tower_regions=set()
    try:
        stack_instances=cloudformation_client.list_stack_instances(
            StackSetName="AWSControlTowerBP-BASELINE-CONFIG"
        )
        for stack in stack_instances['Summaries']:
            control_tower_regions.add(stack['Region'])
    except ClientError as error:
        print(error)
    print(f"Control Tower Regions: {list(control_tower_regions)}")
    return list(control_tower_regions)

def get_all_accounts():
    all_accounts=[]
    active_accounts=[]
    token_tracker={}
    while True:
        member_accounts=org_client.list_accounts(
            **token_tracker
        )
        all_accounts.extend(member_accounts['Accounts'])
        if 'NextToken' in member_accounts:
            token_tracker['NextToken'] = member_accounts['NextToken']
        else:
            break
    for account in all_accounts:
        if account['Status'] == 'ACTIVE':
            active_accounts.append(account)
    return active_accounts