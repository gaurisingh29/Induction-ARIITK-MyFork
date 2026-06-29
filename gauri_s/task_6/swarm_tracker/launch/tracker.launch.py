from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node
import os
from ament_index_python.packages import get_package_share_directory


def generate_launch_description():
    pkg_share = get_package_share_directory('swarm_tracker')
    bridge_config = os.path.join(pkg_share, 'config', 'gz_bridge.yaml')
    #This looks inside the compiled workspace installation directory for your package (swarm_tracker) and finds the gz_bridge.yaml file. 
    #This YAML file contains the exact mapping rules for translating data between Gazebo and ROS 2.

    return LaunchDescription([
        # ---- ROS<->Gazebo topic bridge (camera image/info for iris_2's gimbal cam) ----
        Node(
            package='ros_gz_bridge',
            executable='parameter_bridge',
            name='gz_bridge',
            output='screen',
            arguments=['--ros-args', '-p', f'config_file:={bridge_config}'],
        ),

        # ---- MAVROS for iris_1 (leader) ----
        Node(
            package='mavros',
            executable='mavros_node',
            name='mavros_iris1',
            namespace='iris_1',
            output='screen',
            parameters=[{
                'fcu_url': 'udp://127.0.0.1:14550@',  # SITL instance 0 GCS output
                'gcs_url': '',
                'target_system_id': 1,
                'target_component_id': 1,
            }],
        ),#It isolates this translator into the iris_1 namespace. 
        #It listens on UDP port 14550 (the flight controller's network output for the first drone instance) and targets drone system_id: 1

        # ---- MAVROS for iris_2 (follower) ----
        Node(
            package='mavros',
            executable='mavros_node',
            name='mavros_iris2',
            namespace='iris_2',
            output='screen',
            parameters=[{
                'fcu_url': 'udp://127.0.0.1:14560@',  # SITL instance 1 GCS output
                'gcs_url': '',
                'target_system_id': 2,
                'target_component_id': 1,
            }],
        ),

        # ---- Your nodes ----
        Node(
            package='swarm_tracker',
            executable='leader_evasion_node',
            name='leader_evasion_node',
            output='screen',
        ),
        Node(
            package='swarm_tracker',
            executable='follower_node',
            name='follower_node',
            output='screen',
        ),
    ])