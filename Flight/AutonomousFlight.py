import PID, time, math

class FlightVector(object):
	x = 0
	y = 0
	z = 0

  def __init__(self, x, y, z):
    self.x = float(x)
    self.y = float(y)
    self.z = float(z)

  # String representation
  def __str__(self):
    return '<%s, %s, %s>' % (self.x, self.y, self.z)

  # Produce a copy of itself
  def __copy(self):
    return FlightVector(self.x, self.y, self.z)

  # Signing
  def __neg__(self):
    return FlightVector(-self.x, -self.y, -self.z)

  # Scalar Multiplication
  def __mul__(self, number):
    return FlightVector(self.x * number, self.y * number, self.z * number)

  def __rmul__(self, number):
    return self.__mul__(number)

  # Division
  def __div__(self, number):
    return self.__copy() * (number**-1)

  # Arithmetic Operations
  def __add__(self, operand):
    return FlightVector(self.x + operand.x, self.y + operand.y, self.z + operand.z)

  def __sub__(self, operand):
    return self.__copy() + -operand

  # Cross product
  # cross = a ** b
  def __pow__(self, operand):
    return FlightVector(self.y*operand.z - self.z*operand.y, 
                      self.z*operand.x - self.x*operand.z, 
                      self.z*operand.y - self.y*operand.x)

  # Dot Project
  # dp = a & b
  def __and__(self, operand):
    return (self.x * operand.x) + \
            (self.y * operand.y) + \
            (self.z * operand.z)

  # Operations

  def normal(self):
    return self.__copy() / self.magnitude()

  def magnitude(self):
    return (self.x**2 + self.y**2 + self.z**2)**(.5)


class PIDFlightController(object):
  YAW_MID = 1494.0
  PITCH_MID = 1494.0
  ROLL_MID = 1494.0
  THRUST_LOW = 986.0
  PITCH_P = 10.0
  PITCH_I = 0.0
  PITCH_D = 15.0
  ROLL_P = 10.0
  ROLL_I = 0.0
  ROLL_D = 15.0
  YAW_P = 2.0
  YAW_I = 0.0
  YAW_D = 9.0
  THROTTLE_P = 15.0
  THROTTLE_I = 0.0
  THROTTLE_D = 10.0
  ROLL_CHANNEL = '1'
  PITCH_CHANNEL = '2'
  THROTTLE_CHANNEL = '3'
  YAW_CHANNEL = '4'
  PID_UPDATE_TIME = 0.01

  def __init__(self, vehicle):
    self.vehicle = vehicle
    self.controllers_initialized = False

    self.ThrottlePID = None
    self.RollPID = None
    self.PitchPID = None
    self.YawPID = None
    self.ThrottlePWM = self.THRUST_LOW
    self.RollPWM = self.ROLL_MID
    self.PitchPWM = self.PITCH_MID
    self.YawPWM = self.YAW_MID
    self.initialize_controllers()

  def initialize_controllers(self):
    if not controllers_initialized:
      self.PitchPID = PID.PID(PITCH_P, PITCH_I, PITCH_D)
      self.PitchPID.SetPoint = desired_pitch_velocity
      self.PitchPID.setSampleTime(PID_UPDATE_TIME)

      self.RollPID = PID.PID(ROLL_P, ROLL_I, ROLL_D)
      self.RollPID.SetPoint = desired_roll_velocity
      self.RollPID.setSampleTime(PID_UPDATE_TIME)
      
      self.YawPID = PID.PID(YAW_P , YAW_I, YAW_D)
      self.YawPID.SetPoint = desired_yaw_angle
      self.YawPID.setSampleTime(PID_UPDATE_TIME)

      self.ThrottlePID = PID.PID(THROTTLE_P, THROTTLE_I, THROTTLE_D)
      self.ThrottlePID.SetPoint = desired_speed
      self.ThrottlePID.setSampleTime(PID_UPDATE_TIME)
      self.controllers_initialized = True

  def send_velocity_vector(self, requested_flight_vector, yaw_angle=None):
    self.PitchPID.SetPoint = requested_flight_vector.x
    self.RollPID.SetPoint = requested_flight_vector.y

    if(yaw_angle):
      self.YawPID.SetPoint = self.get_yaw_radians(yaw_angle)

    self.ThrottlePID.SetPoint = requested_flight_vector.z

  def update_controllers(self):
    self.PitchPID.update(vehicle.velocity[0])
    self.RollPID.update(vehicle.velocity[1])
    self.YawPID.update(self.vehicle.attitude.yaw)
    self.ThrottlePID.update(vehicle.velocity[2])

    self.PitchPWM -= self.PitchPID.output
    self.RollPWM += self.RollPID.output                
    self.YawPWM += self.YawPID.output
    self.ThrottlePWM += self.ThrottlePWM.output
  
  def write_to_rc_channels(self, flushChannels=False):
    
    if(self.flushChannels):
      self.ThrottlePWM = self.THRUST_LOW
      self.RollPWM = self.ROLL_MID
      self.PitchPWM = self.PITCH_MID
      self.YawPWM = self.YAW_MID
    
    self.vehicle.channels.overrides[PITCH_CHANNEL] = self.PitchPWM
    self.vehicle.channels.overrides[ROLL_CHANNEL] = self.RollPWM
    self.vehicle.channels.overrides[YAW_CHANNEL] = self.YawPWM
    self.vehicle.channels.overrides[THROTTLE_CHANNEL] = self.ThrottlePWM


  def get_yaw_radians(self, angle):
    if angle < 180:
        return math.radians(angle)
    else:
        return math.radians(angle-180) -  math.pi

  def convert_velocity_to_pwm(desired_velocity):
    return  ((( 512.0 * desired_velocity ) / 5.0 ) + 1494.0)