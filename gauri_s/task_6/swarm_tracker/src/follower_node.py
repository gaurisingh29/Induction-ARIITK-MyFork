#!/usr/bin/env python3

import rclpy
from rclpy.node import Node
from sensor_msgs.msg import Image
# sensor_msgs/Image is the ROS standard message for camera frames.
# Each Image message contains width, height, encoding (e.g. "bgr8"), and raw pixel bytes.
from geometry_msgs.msg import Twist # geometry_msgs/Twist encodes linear + angular velocity in 3D.
import cv2
from cv_bridge import CvBridge    # Converts ROS Image msgs <--> OpenCV numpy arrays
import cv2.aruco as aruco
from mavros_msgs.msg import MountControl # MountControl lets us command the gimbal's pitch/yaw angles directly via MAVROS.
# It maps to the MAVLink MOUNT_CONTROL message.
import numpy as np
import time #for derivative timestamp tracking

# PID CONTROLLER
# ─────────────────────────────────────────────────────────────────────────────
class PIDController:
    """
    A generic Proportional-Integral-Derivative controller.
 
    WHY PID?
    --------
    A pure P controller: output = Kp * error
      → Oscillates around the target. As you approach the target, output shrinks,
        but by the time it shrinks to zero, you've overshot.
 
    Adding D (derivative): output += Kd * (error - prev_error) / dt
      → Acts like damping — it opposes how FAST the error is changing.
        If you're converging quickly, D slows you down before you overshoot.
 
    Adding I (integral): output += Ki * integral_of_error * dt
      → Eliminates steady-state offset. If P+D holds you 5 cm short of target,
        the integral slowly builds up and pushes you the rest of the way.
 
    VISUAL ANALOGY:
      - P = springiness (pulls toward target)
      - D = damping (resists fast changes)
      - I = slow pressure that eliminates offset
 

    """
 
    def __init__(self, kp, ki, kd, output_limit=2.0):
        """
        kp          — Proportional gain. Higher = faster response, more overshoot.
        ki          — Integral gain. Higher = faster offset elimination, more oscillation.
        kd          — Derivative gain. Higher = more damping, but amplifies noise.
        output_limit — Clips output to [-limit, +limit] to prevent actuator saturation.
        """
        self.kp = kp
        self.ki = ki
        self.kd = kd
        self.output_limit = output_limit
 
        self._prev_error = 0.0      # Error from the last tick (for derivative)
        self._integral   = 0.0      # Accumulated error over time (for integral)
        self._last_time  = None     # Timestamp of last update (seconds)
 
    def reset(self):
        """Call this when the marker is lost so integral doesn't wind up."""
        self._integral   = 0.0
        self._prev_error = 0.0
        self._last_time  = None
 
    def update(self, error: float) -> float:
        """
        Compute the PID output for the given error.
 
        error  — (setpoint - measured_value).  Positive means we need to move "more".
        returns — signed velocity/angle command in whatever units make sense for the axis.
        """
        now = time.time()
 
        # On the very first call there's no dt, so skip D and I terms
        if self._last_time is None:
            self._last_time = now
            self._prev_error = error
            return float(np.clip(self.kp * error, -self.output_limit, self.output_limit))
 
        dt = now - self._last_time          # Time since last update (seconds)
        if dt <= 0:
            dt = 1e-6                       # Guard against zero division
 
        # P term: proportional to current error
        p_term = self.kp * error
 
        # I term: integral accumulates error × time
        # We clamp the integral itself ("integral windup prevention") to avoid
        # situations where a long period of large error causes an unrecoverable
        # buildup that keeps commanding full speed even after the error is gone.
        self._integral += error * dt
        self._integral  = float(np.clip(self._integral, -10.0, 10.0))  # anti-windup
        i_term = self.ki * self._integral
 
        # D term: rate of change of error (finite difference approximation)
        d_term = self.kd * (error - self._prev_error) / dt
 
        # Update state for next call
        self._prev_error = error
        self._last_time  = now
 
        output = p_term + i_term + d_term
        return float(np.clip(output, -self.output_limit, self.output_limit))
 
 
# ─────────────────────────────────────────────────────────────────────────────
# GIMBAL CONTROLLER
# ─────────────────────────────────────────────────────────────────────────────
class GimbalController:
    """
    Controls the camera gimbal to keep the ArUco marker centred in the frame.
 
    HOW A GIMBAL WORKS IN GAZEBO/MAVROS
    =====================================
    In the SDF model, iris_2 has a camera mounted on a 2-axis gimbal:
      - Pitch axis: tilts the camera up/down
      - Yaw  axis: pans the camera left/right
 
    MAVROS exposes gimbal control via the /mavros/mount_control/command topic.
    We publish desired pitch and yaw angles in degrees.
 
    WHY DO WE NEED A GIMBAL PID?
    ==============================
    The drone itself is flying and pitching/rolling slightly. Without gimbal
    stabilisation, the camera view would wobble and we'd lose lock on the marker.
    The gimbal corrects for drone attitude + actively aims at the target.
 
    PIXEL ERROR → ANGLE COMMAND
    ============================
    If the marker is 50 pixels to the right of centre in a 640-wide image,
    the angular error in the camera frame is approximately:
        angle_error ≈ pixel_offset x (fov / image_width)
    We feed this pixel offset directly to a PID that outputs a gimbal angle delta.
    """
 
    def __init__(self, node: Node, image_width=640, image_height=480):
        # Publisher to MAVROS mount control topic
        # MountControl message fields we use:
        #   .mode     = 2 means "GPS-point" tracking mode (but we'll override angles)
        #   .pitch    = desired pitch angle in centidegrees
        #   .yaw      = desired yaw angle in centidegrees
        self.pub = node.create_publisher(
            MountControl,
            '/iris_2/mavros/mount_control/command',
            10
        )
 
        self.image_cx = image_width  / 2.0     # Image centre x (pixels)
        self.image_cy = image_height / 2.0     # Image centre y (pixels)
 
        # PID for yaw   (left/right pixel error)
        # PID for pitch (up/down   pixel error)
        # Start with conservative gains; tune up if too slow, down if oscillating
        self.pid_yaw   = PIDController(kp=0.05, ki=0.005, kd=0.01, output_limit=30.0)
        self.pid_pitch = PIDController(kp=0.05, ki=0.005, kd=0.01, output_limit=30.0)
 
        self._current_yaw   = 0.0   # Accumulated gimbal angles (degrees)
        self._current_pitch = 0.0
 
    def update(self, marker_cx_pixels: float, marker_cy_pixels: float):
        """
        Given the marker's centre pixel position, update gimbal angles.
 
        marker_cx_pixels — horizontal centre of detected marker in pixels
        marker_cy_pixels — vertical centre of detected marker in pixels
        """
        # Error = how far the marker is from the image centre, in pixels
        # Positive x_err → marker is to the right → we need to yaw RIGHT
        # Positive y_err → marker is below centre  → we need to pitch DOWN 
        x_err = marker_cx_pixels - self.image_cx   # pixels (+ = right)
        y_err = marker_cy_pixels - self.image_cy   # pixels (+ = below)
 
        # PID outputs are delta angles in degrees
        yaw_delta   =  self.pid_yaw.update(x_err)  # positive = rotate right
        pitch_delta = -self.pid_pitch.update(y_err) # negative because down = negative pitch
 
        # Accumulate into current gimbal angle
        self._current_yaw   += yaw_delta
        self._current_pitch += pitch_delta
 
        # Physical gimbal limits (prevent hitting mechanical stops)
        self._current_yaw   = float(np.clip(self._current_yaw,   -90.0,  90.0))
        self._current_pitch = float(np.clip(self._current_pitch, -90.0,  25.0))
 
        # Publish to MAVROS
        msg = MountControl()
        msg.mode  = 2                                       # Angle control mode
        msg.pitch = self._current_pitch * 100.0            # centidegrees
        msg.yaw   = self._current_yaw   * 100.0            # centidegrees
        self.pub.publish(msg)
 
    def reset_to_forward(self):
        """Point the gimbal straight ahead (called when marker is lost)."""
        self._current_yaw   = 0.0
        self._current_pitch = 0.0
        msg = MountControl()
        msg.mode  = 2
        msg.pitch = 0.0
        msg.yaw   = 0.0
        self.pub.publish(msg)
 


class FollowerNode(Node):
    '''This class subscribes to the camera image, runs ArUco detection, computes PID outputs, and publishes Twist commands. 
    It talks to everything else via ROS 2 topics.
    
    OpenCV's estimatePoseSingleMarkers gives you tvec = [x, y, z] in the
    CAMERA frame (also called "camera optical frame"):
 
        z — points FORWARD (into the scene, toward the marker)
        x — points RIGHT
        y — points DOWN
 
    MAVROS cmd_vel uses the drone's BODY frame:
        linear.x — forward in drone body frame
        linear.y — left   in drone body frame
        linear.z — up     in drone body frame
 
    So we need to map:
        tvec[2] (camera forward/distance) → linear.x (drone forward speed)
        tvec[0] (camera right offset)     → linear.y (drone lateral speed, negated)
        tvec[1] (camera up/down)          → linear.z (drone altitude, negated)
    
    '''

 # Subtask 3: landing pad marker ID.  Formation uses ID 0 on iris_1.
    # When iris_1 enters landing zone, iris_2 switches to tracking this ID.
    FORMATION_MARKER_ID = 0
    LANDING_PAD_MARKER_ID = 5      # Different marker on the ground landing pad
 
    # Phase state machine
    PHASE_FORMATION = "formation"
    PHASE_LANDING   = "landing"

    def __init__(self):
        super().__init__('follower_node')

         # ── Parameters ────────────────────────────────────────────────────────
        self.target_distance = 2.0      # metres to maintain from iris_1
        self.IMAGE_WIDTH     = 640      # Must match Gazebo camera SDF
        self.IMAGE_HEIGHT    = 480

        # Subscribe to the gimbal camera image topic from iris_2
        # TODO: Update with the correct topic name for your setup
        self.image_sub = self.create_subscription(
            Image,
            '/camera/image_raw',
            self.image_callback,
            10
        )
        
        # Publisher for velocity commands via MAVROS
        # TODO: Ensure the drone is in GUIDED/OFFBOARD mode and armed before publishing
        self.vel_pub = self.create_publisher(
            Twist,
            '/iris_2/mavros/setpoint_velocity/cmd_vel_unstamped',
            10
        )
        
        self.bridge = CvBridge()
        
        # ArUco dictionary and parameters
        self.aruco_dict = aruco.getPredefinedDictionary(aruco.DICT_4X4_50)
        self.aruco_params = aruco.DetectorParameters()
        
        # Camera intrinsic matrix (Assume standard parameters or calibrate)

  # ── Camera Intrinsics ─────────────────────────────────────────────────
        # The camera matrix K maps 3D camera-frame points to 2D image pixels:
        #   [u]   [fx  0  cx] [X/Z]
        #   [v] = [ 0 fy  cy] [Y/Z]    where (u,v) = pixel, (X,Y,Z) = camera coords
        #   [1]   [ 0  0   1] [ 1 ]
        #
        # fx, fy = focal length in pixels  (≈ image_width / (2 * tan(hfov/2)))
        # cx, cy = principal point ≈ image centre
        #
        # TODO: Replace with values from: ros2 topic echo /iris_2/camera/camera_info
        # A Gazebo camera with hfov=1.3962 radians (80°) and 640×480 gives fx≈457 px.

        # TODO: Update with actual camera intrinsics from Gazebo
        self.camera_matrix = np.array([[530.0, 0.0, 320.0],
                                       [0.0, 530.0, 240.0],
                                       [0.0, 0.0, 1.0]])
        self.dist_coeffs = np.zeros((4,1))   # Gazebo cameras have no distortion, so zeros are correct here.
        
        self.target_distance = 2.0 # meters
        self.get_logger().info("Follower Node initialized. Waiting for images...")

    def image_callback(self, msg):
        """
        Called every time a new camera frame arrives from Gazebo.
 
        This is the heart of the node. It runs the full vision + control pipeline.
        """


        # Step 1: Convert ROS Image message → OpenCV numpy array
        # imgmsg_to_cv2(msg, desired_encoding) returns an ndarray of shape (H, W, 3)
        # with uint8 values 0–255.
        try:
            cv_image = self.bridge.imgmsg_to_cv2(msg, "bgr8")
        except Exception as e:
            self.get_logger().error(f"Failed to convert image: {e}")
            return

        gray = cv2.cvtColor(cv_image, cv2.COLOR_BGR2GRAY)  # pattern doesn't carry colour information anyway.
        
        # Detect ArUco markers
        # detectMarkers returns:
        #   corners — list of arrays, each shape (1, 4, 2):  4 corner points in pixels
        #   ids     — array of shape (N, 1): the integer ID for each detected marker
        #   rejected — list of candidate regions that were rejected (debug use only)
        corners, ids, rejected = aruco.detectMarkers(gray, self.aruco_dict, parameters=self.aruco_params)
        

        # Step 4: Determine which marker ID we're currently tracking
        active_id = (
            self.LANDING_PAD_MARKER_ID if self.phase == self.PHASE_LANDING
            else self.FORMATION_MARKER_ID
        )


        if ids is not None:
            aruco.drawDetectedMarkers(cv_image, corners, ids)   # Draw bounding boxes and IDs on the display image
                        
            target_idx = self._find_target_marker(ids, active_id) # Find our target marker among all detected markers
            
            if target_idx is not None:
                self.frames_without_marker = 0  # Reset search counter


        # Estimate pose of each marker


        # Returns:
                #   rvecs — rotation    vectors (1, 1, 3), Rodrigues format
                #   tvecs — translation vectors (1, 1, 3) in METRES, camera frame
                #
                # tvec[0] = [x, y, z]:
                #   x = lateral offset  (positive = marker to the RIGHT)
                #   y = vertical offset (positive = marker BELOW centre)
                #   z = depth (forward distance) from camera to marker

            # Marker size is assumed to be 0.15m x 0.15m (based on SDF)
                rvecs, tvecs, _ = aruco.estimatePoseSingleMarkers(corners, 0.15, self.camera_matrix, self.dist_coeffs)
            
                for i in range(len(ids)):
                    cv2.drawFrameAxes(cv_image, self.camera_matrix, self.dist_coeffs, rvecs[i], tvecs[i], 0.1)
                
                # tvecs[i][0] contains [x, y, z] distance from camera to marker
                    distance = tvecs[i][0][2]
                    x_offset = tvecs[i][0][0]




                
                # TODO: Implement your control logic here!
                # 1. Calculate error based on target_distance and x_offset
                # 2. Compute PID outputs for linear.x and angular.z
                # 3. Publish to self.vel_pub
                


            # Extract the translation vector for the target marker
                tvec = tvecs[0][0]           # shape (3,)
                x_offset  = tvec[0]          # metres: right is positive
                y_offset  = tvec[1]          # metres: down is positive
                z_distance = tvec[2]         # metres: distance from camera to marker
    
                # Gimbal update: compute marker centre in pixels for gimbal PID
                corner_pts = corners[target_idx][0]   # shape (4, 2)
                marker_cx = float(np.mean(corner_pts[:, 0]))  # mean of 4 x-coords
                marker_cy = float(np.mean(corner_pts[:, 1]))  # mean of 4 y-coords
                self.gimbal.update(marker_cx, marker_cy)
 
                # Log telemetry
                # self.get_logger().info(
                #         f"[{self.phase}] Marker {active_id} | "
                #         f"Z:{z_distance:.2f}m  X:{x_offset:.2f}m  Y:{y_offset:.2f}m"
                #     )
                self.get_logger().info(f"Marker {ids[i][0]} detected at Z: {distance:.2f}m, X: {x_offset:.2f}m")

 
                # Step 7: Run the appropriate control law
                if self.phase == self.PHASE_FORMATION:
                    self._formation_control(z_distance, x_offset, y_offset)
                else:
                    self._landing_control(z_distance, x_offset, y_offset)
            else:
                # Target ID not in this frame — could be partially detected
                self._handle_marker_loss()
        else:
            # No markers at all detected
            self._handle_marker_loss()
            # TODO: Handle marker loss. Spin to search or hover?
            


        # Display the feed (ensure you have an X-server running if doing this in Docker)
        cv2.imshow("Follower Camera", cv_image)
        cv2.waitKey(1)   # 1 ms wait — keeps the window responsive without blocking
        # Requires an X server (DISPLAY env variable) or virtual framebuffer (Xvfb).
        # In Docker: run with -e DISPLAY=$DISPLAY -v /tmp/.X11-unix:/tmp/.X11-unix



#----------------------------------------------------------------------------------------------------------------------
#-------------------------------------------------------------------------------------------------------------
#-------------------------------------------------------------------------------------------------------------
#-------------------------------------------------------------------------------------------------------------






    # ── Formation Control ──────────────────────────────────────────────────────
    def _formation_control(self, z_distance: float, x_offset: float, y_offset: float):
        """
        Subtask 1 & 2: Maintain 2 m behind iris_1 using pure visual feedback.
 
        ERROR DEFINITIONS
        -----------------
        distance_error  = (current_z_distance - target_distance)
            + means too far   → need to fly FORWARD  → positive linear.x
            - means too close → need to fly BACKWARD  → negative linear.x
 
        lateral_error   = x_offset (direct camera-frame lateral displacement)
            + means marker is to the RIGHT → we need to drift RIGHT → positive linear.y
              BUT in drone body frame, positive y is LEFT. So we negate.
 
        vertical_error  = y_offset (camera y, positive = below centre)
            + means marker is BELOW us → drone is too HIGH → descend → negative linear.z
              OR marker is below because the leader descended → follow down.
        """
        # Distance error: how far we are from the desired 2 m spacing
        distance_error = z_distance - self.target_distance   # metres
 
        # Lateral error: how far the marker is off-centre horizontally
        lateral_error  = x_offset                            # metres
 
        # Vertical error: how far the marker is off-centre vertically
        vertical_error = y_offset                            # metres
 
        # Compute PID outputs (metres per second)
        cmd_forward = self.pid_distance.update(distance_error)
        cmd_lateral = self.pid_lateral.update(lateral_error)
        cmd_vertical = self.pid_vertical.update(vertical_error)
 
        # Build Twist message
        msg = Twist()
        #   Forward/back: positive = fly toward marker (reduce distance)
        msg.linear.x =  cmd_forward
 
        #   Lateral: negate because camera +x (right) = drone body -y in NED/ENU
        #   (depends on your specific MAVROS configuration — test and flip if wrong)
        msg.linear.y = -cmd_lateral
 
        #   Vertical: negate because camera +y (down) = drone body -z (down)
        msg.linear.z = -cmd_vertical
 
        self.vel_pub.publish(msg)
 
    # ── Landing Control ────────────────────────────────────────────────────────
    def _landing_control(self, z_distance: float, x_offset: float, y_offset: float):
        """
        Subtask 3: Precision landing on a moving pad.
 
        In landing phase the marker is the GROUND PAD (different ID).
        The camera (now pointing more downward) gives:
          z_distance ≈ altitude above pad
          x_offset   ≈ horizontal misalignment (left/right)
          y_offset   ≈ horizontal misalignment (forward/backward in camera frame)
 
        Strategy:
          1. Use PIDs to null x and y offsets → hover directly above pad.
          2. Descend at a slow constant rate while offsets are small.
          3. Below 0.3 m, trigger LAND mode via MAVROS.
        """
        DESCENT_RATE      = 0.3    # m/s descend speed
        ALIGNMENT_THRESH  = 0.05   # metres — alignment tolerance before descending
 
        # Null the horizontal offsets first
        cmd_lateral  = self.pid_lateral.update(x_offset)
        cmd_vertical = self.pid_vertical.update(y_offset)
 
        msg = Twist()
        msg.linear.y = -cmd_lateral
        msg.linear.x = -cmd_vertical   # y_offset maps to forward/back during landing
 
        # Only descend if reasonably aligned
        if abs(x_offset) < ALIGNMENT_THRESH and abs(y_offset) < ALIGNMENT_THRESH:
            msg.linear.z = -DESCENT_RATE   # Descend (negative = down)
        else:
            msg.linear.z = 0.0              # Hold altitude, correct alignment first
 
        self.vel_pub.publish(msg)
 
        self.get_logger().info(
            f"[LANDING] alt≈{z_distance:.2f}m  "
            f"lat_err:{x_offset:.3f}m  fwd_err:{y_offset:.3f}m"
        )
 
    # ── Marker Loss Handler ────────────────────────────────────────────────────
    def _handle_marker_loss(self):
        """
        Called when the target marker is not visible.
 
        Strategy:
          - For the first SEARCH_THRESHOLD frames: hover in place (zero velocity).
          - After threshold: initiate a slow yaw rotation to search for the marker.
          - Also reset PID integral term to prevent windup during the gap.
        """
        self.frames_without_marker += 1
 
        # Reset PID integrals so stale integration doesn't cause a lurch when re-acquired
        self.pid_distance.reset()
        self.pid_lateral.reset()
        self.pid_vertical.reset()
 
        if self.frames_without_marker < self.SEARCH_THRESHOLD:
            # Hover: publish zero velocity
            msg = Twist()   # All zeros by default
            self.vel_pub.publish(msg)
            self.get_logger().warn(
                f"Marker lost ({self.frames_without_marker}/{self.SEARCH_THRESHOLD} frames). Hovering."
            )
        else:
            # Slow yaw search (spin to find the marker)
            msg = Twist()
            msg.angular.z = 0.2    # rad/s — gentle counter-clockwise yaw
            self.vel_pub.publish(msg)
            self.get_logger().warn("Marker lost > threshold. Searching (yawing)...")
 
    # ── Subtask 3: Phase Transition ────────────────────────────────────────────
    def trigger_landing_phase(self):
        """
        Call this method (e.g. from a ROS service or timer) to switch iris_2
        from formation flight to precision landing mode.
 
        Resets all PIDs and gimbal to avoid transient spikes on mode transition.
        """
        self.get_logger().info("=== PHASE TRANSITION: FORMATION → LANDING ===")
        self.phase = self.PHASE_LANDING
 
        # Reset all PID states to zero — prevents integral windup carrying over
        self.pid_distance.reset()
        self.pid_lateral.reset()
        self.pid_vertical.reset()
 
        # Point gimbal downward for landing pad detection
        # We tilt -45° pitch to look at the ground while hovering overhead
        # You'd gradually sweep to -90° (straight down) during final descent
        # For now, call a tilt command manually:
        msg = MountControl()
        msg.mode  = 2
        msg.pitch = -4500.0    # -45 degrees in centidegrees
        msg.yaw   = 0.0
        self.gimbal.pub.publish(msg)
 
    # ── Utility ───────────────────────────────────────────────────────────────
    @staticmethod
    def _find_target_marker(ids: np.ndarray, target_id: int):
        """
        Given the array of detected IDs, return the index of the target ID.
        ids has shape (N, 1) where N is number of detected markers.
        Returns None if target not found.
        """
        for i, id_arr in enumerate(ids):
            if id_arr[0] == target_id:
                return i
        return None
 



# ENTRY POINT
# ─────────────────────────────────────────────────────────────────────────────

def main(args=None):
    rclpy.init(args=args) # Initialise ROS 2 runtimec
    node = FollowerNode()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        cv2.destroyAllWindows()
        rclpy.shutdown()

if __name__ == '__main__':
    main()