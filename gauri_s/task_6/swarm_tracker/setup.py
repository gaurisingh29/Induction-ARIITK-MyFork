'''This script is a setup.py file for a Python-based ROS 2 (Robot Operating System) package named swarm_tracker.

Its main job is to tell the ROS 2 build system (colcon) exactly how to compile, install, 
and map the files for your drone swarm package so that ROS 2 can recognize and run your nodes.'''
from setuptools import setup
import os
from glob import glob

package_name = 'swarm_tracker'

setup(
    name=package_name,
    version='0.1.0',
    packages=[package_name],
    data_files=[
        ('share/ament_index/resource_index/packages', ['resource/' + package_name]),        #(resource/): Registers the package with ament (the ROS 2 internal index) so commands like ros2 pkg list know this package exists.
        ('share/' + package_name, ['package.xml']),
        (os.path.join('share', package_name, 'launch'), glob('launch/*.launch.py')),    #(launch/*.launch.py): Looks inside your local launch/ folder and installs all Python launch files. This allows you to run ros2 launch swarm_tracker <file_name>.launch.py.
        (os.path.join('share', package_name, 'config'), glob('config/*.yaml')),
    ],
    install_requires=['setuptools'],
    zip_safe=True,
    maintainer='Gauri Singh',
    description='Leader-follower drone swarm with ArUco-based visual servoing',
    license='MIT',
    tests_require=['pytest'],
    
    
    
    entry_points={
        'console_scripts': [
            'leader_evasion_node = swarm_tracker.leader_evasion_node:main',
            'follower_node = swarm_tracker.follower_node:main',
        ],
    },
    #This maps custom terminal commands directly to the main() functions inside your Python scripts. Once compiled, instead of executing raw Python files, you can run them natively using standard ROS 2 CLI execution:

    #Running ros2 run swarm_tracker leader_evasion_node will execute the main() function inside swarm_tracker/leader_evasion_node.py.
    #Running ros2 run swarm_tracker follower_node will execute the main() function inside swarm_tracker/follower_node.py.
)