#!/usr/bin/env python3

import rclpy
from rclpy.node import Node
from geometry_msgs.msg import Twist
import math
import time

class LeaderEvasionNode(Node):
    '''Sets up a publisher to send velocity commands to a specific topic'''
    def __init__(self):
        super().__init__('leader_evasion_node')
        
        # Publisher for velocity commands via MAVROS
        self.vel_pub = self.create_publisher(
            Twist,
            '/iris_1/mavros/setpoint_velocity/cmd_vel_unstamped',
            10
        ) 
        #/iris_1/mavros/setpoint_velocity/cmd_vel_unstamped
        #This topic is typically used to control the velocity of drones/robots in MAVROS, 
        #a communication driver for MAVLink compatible autopilots.
        
        self.timer = self.create_timer(0.1, self.timer_callback) #Sets up a timer to call timer_callback() every 0.1 seconds
        self.start_time = time.time()
        self.get_logger().info("Leader Evasion Node started. Executing maneuvers...")

    def timer_callback(self):
        t = time.time() - self.start_time
        
        msg = Twist() #msg is an object of the Twist class
        # Complex figure-eight and altitude changing maneuver
        msg.linear.x = 2.0 * math.sin(0.5 * t)
        msg.linear.y = 1.5 * math.cos(0.2 * t)
        msg.linear.z = 0.5 * math.sin(0.3 * t)
        msg.angular.z = 0.5 * math.cos(0.4 * t)
        
        self.vel_pub.publish(msg)

def main(args=None):
    rclpy.init(args=args) #rclpy= ROS 2 Client Library for Python
    #the init() function present in the rclpy package must be called before doing anything ROS-related
    node = LeaderEvasionNode() #saves the custom drone control node we have defined into a variable called "node"
    try:
        rclpy.spin(node) #spin() function keeps the node running so the 10Hz timer can keep continuously publishing velocity commands to the drone
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node() #deletes the node, stops your publishers, and releases the memory it was using
        rclpy.shutdown() #closes the ROS 2 network sockets for this script

if __name__ == '__main__':
    main()