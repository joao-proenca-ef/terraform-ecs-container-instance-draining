import os
import json
import time
import boto3

CLUSTER = os.environ.get('CLUSTER')
REGION = os.environ.get('REGION')
SLEEP_TIME = 10

ECS = boto3.client('ecs', region_name=REGION)
ASG = boto3.client('autoscaling', region_name=REGION)
SNS = boto3.client('sns', region_name=REGION)
def check_active_instance(inst):
  if(inst['status'] == 'ACTIVE'):
    return True
  else:
    return False
def find_ecs_instance_info(instance_id):
    paginator = ECS.get_paginator('list_container_instances')
    for list_resp in paginator.paginate(cluster=CLUSTER):
        arns = list_resp['containerInstanceArns']
        desc_resp = ECS.describe_container_instances(cluster=CLUSTER,
                                                     containerInstances=arns)
        num_active_ecs_instances = len(list(filter(check_active_instance, desc_resp['containerInstances'])));
        for container_instance in desc_resp['containerInstances']:
            if container_instance['ec2InstanceId'] != instance_id:
                continue
            print('Found instance: id=%s, arn=%s, status=%s, runningTasksCount=%s' %
                  (instance_id, container_instance['containerInstanceArn'],
                   container_instance['status'], container_instance['runningTasksCount']))
            return (container_instance['containerInstanceArn'],
                    container_instance['status'], container_instance['runningTasksCount'], num_active_ecs_instances, len(desc_resp['containerInstances']))
    return None, None, 0
def instance_has_running_tasks(instance_id, autoscalinggroup):
    (instance_arn, container_status, running_tasks, num_active_ecs_instances, num_ecs_instances) = find_ecs_instance_info(instance_id)
    if instance_arn is None:
        print('Could not find instance ID %s. Letting autoscaling kill the instance.' %
              (instance_id))
        return False
    ecs_autoscaling = ASG.describe_auto_scaling_groups(AutoScalingGroupNames=[autoscalinggroup])
    # NumOfInstances = len(ecs_autoscaling['Instances'])
    # NumOfInstances <= ecs_autoscaling['DesiredCapacity'] and

    print('We are running  %d active instances with %d desired capacity' % (num_active_ecs_instances, ecs_autoscaling['AutoScalingGroups'][0]['DesiredCapacity']))

    if container_status != 'DRAINING':
        if num_active_ecs_instances <= ecs_autoscaling['AutoScalingGroups'][0]['DesiredCapacity']:
          print('Lets wait until we have more active instances (%d) than desired capacity (%d)' % (num_active_ecs_instances, ecs_autoscaling['AutoScalingGroups'][0]['DesiredCapacity']))
          return True

        print('Setting container instance %s (%s) to DRAINING' %
              (instance_id, instance_arn))
        ECS.update_container_instances_state(cluster=CLUSTER,
                                             containerInstances=[instance_arn],
                                             status='DRAINING')
    return running_tasks > 0
def lambda_handler(event, context):
    msg = json.loads(event['Records'][0]['Sns']['Message'])
    if 'LifecycleTransition' not in msg.keys() or \
       msg['LifecycleTransition'].find('autoscaling:EC2_INSTANCE_TERMINATING') == -1:
        print('Exiting since the lifecycle transition is not EC2_INSTANCE_TERMINATING.')
        return
    if instance_has_running_tasks(msg['EC2InstanceId'], msg['AutoScalingGroupName']):
        print('Tasks are still running on instance %s; posting msg to SNS topic %s' %
              (msg['EC2InstanceId'], event['Records'][0]['Sns']['TopicArn']))
        time.sleep(SLEEP_TIME)
        sns_resp = SNS.publish(TopicArn=event['Records'][0]['Sns']['TopicArn'],
                               Message=json.dumps(msg),
                               Subject='Publishing SNS msg to invoke Lambda again.')
        print('Posted msg %s to SNS topic.' % (sns_resp['MessageId']))
    else:
        print('No tasks are running on instance %s; setting lifecycle to complete' %
              (msg['EC2InstanceId']))
        ASG.complete_lifecycle_action(LifecycleHookName=msg['LifecycleHookName'],
                                      AutoScalingGroupName=msg['AutoScalingGroupName'],
                                      LifecycleActionResult='CONTINUE',
                                      InstanceId=msg['EC2InstanceId'])
