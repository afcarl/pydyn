# -*- coding: utf-8 -*-

import array
import serial
import threading

from pypot.utils import flatten_list, reshape_list
from pypot.dynamixel.conversions import *
from pypot.dynamixel.protocol import *
from pypot.dynamixel.packet import *


class DynamixelIO:
    """ 
        This class handles the low-level communication with robotis motors.

        Using a USB communication device such as USB2DYNAMIXEL or USB2AX,
        you can open serial communication with robotis motors (MX, RX, AX)
        using communication protocols TTL or RS485.

        This class handles low-level IO communication by providing access to the
        different registers in the motors.
        Users can use high-level access such as position, load, torque to control
        the motors.
        
        You can access two different area space of the dynamixel registers: 
            * the EEPROM area
            * the RAM area
        
        When values are written to the EEPROM, they are conserved after cycling the power.
        The values written to the RAM are lost when cycling the power.
        
        .. warning:: When accessing EEPROM registers the motor enters a "busy" mode and should not be accessed before about 100ms.
        
        """
    
    __open_ports = []
    
    
    def __init__(self, port, baudrate=1000000, timeout=1.0):
        """ 
            At instanciation, it opens the serial port and sets the communication parameters.
            
            .. warning:: The port can only be accessed by a single DynamixelIO instance.
            
            :param string port: the serial port to use (e.g. Unix (/dev/tty...), Windows (COM...)).
            :param int baudrate: default for new motors: 57600, for PyPot motors: 1000000
            :param float timeout: read timeout in seconds
            
            :raises: ValueError (when port is already used)
            
            """
        if port in self.__open_ports:
            raise ValueError('Port already used (%s)!' % (port))
        
        self._serial = serial.Serial(port, baudrate, timeout=timeout)
        self.__open_ports.append(port)
        
        self._lock = threading.RLock()
        
        self._motor_models = {}
    
    def __del__(self):
        """ Automatically closes the serial communication on destruction. """
        self.__open_ports.remove(self._serial.port)
        self._serial.close()
    
    def __repr__(self):
        return "<DXL IO: port='%s' baudrate=%d timeout=%.g>" % (self._serial.port,
                                                                self._serial.baudrate,
                                                                self._serial.timeout)
    
    
    def flush_serial_communication(self):
        """
            Flush the serial communication (both input and output).
            
            .. note:: You can use this method after a communication issue (such as a timeout) to refresh the communication bus.
            
            """
        self._serial.flushInput()
        self._serial.flushOutput()
    
    
    # MARK: - Motor discovery
    
    def ping(self, motor_id):
        """
            Pings the motor with the specified id.
            
            :param int motor_id: specified motor id [0-253]
            :return: bool
            :raises: ValueError if the motor id is out of the possible ids range.
            
            """
        if not (0 <= motor_id <= 253):
            raise ValueError('Motor id must be in [0, 253]!')
        
        ping_packet = DynamixelPingPacket(motor_id)
        
        try:
            self._send_packet(ping_packet)
            return True
        
        except DynamixelTimeoutError:
            return False
    
    
    def scan(self, ids=xrange(254)):
        """             
            Finds the ids of all the motors connected to the bus.
            
            :param list ids: the range of ids to search
            :return: list of ids found
            
            """
        return filter(self.ping, ids)
    
    
    def get_model(self, motor_id):
        """
            Finds the model name of the robotis motor.
            
            :param int motor_id: specified motor id [0-253]
            :return: one of the DXL_MODEL_NUMBER values (see :py:mod:`~pypot.dynamixel.protocol`)
            :raises: DynamixelUnsupportedMotorError if the motor model is currently unsupported.
                                    
            """
        model_number = self._send_read_packet(motor_id, 'MODEL_NUMBER')
        
        if not DXL_MODEL_NUMBER.has_key(model_number):
            raise DynamixelUnsupportedMotorError(motor_id, model_number)
        
        motor_model = DXL_MODEL_NUMBER[model_number]
        self._motor_models[motor_id] = motor_model
        return motor_model
    
    # def specify_model(self, motor_id, motor_model):
    #     """
    #         Specify the model of a virtual motor.
    
    #         Parameters
    #         ----------
    #         :param motor_id: int
    #             The motor ID [0-253]
    
    #         """
    #     if motor_model not in DXL_MODEL_NUMBER.values():
    #         raise ValueError('Unknown motor model: %s' % (motor_model))
    
    #     self._motor_models[motor_id] = motor_model
    
    def _lazy_get_model(self, motor_id):
        """ Finds the motor model if it is not already known. """
        if not self._motor_models.has_key(motor_id):
            self.get_model(motor_id)
        
        return self._motor_models[motor_id]
    
    
    # MARK: - Sync Read/Write
    
    def get_sync_positions(self, motor_ids):
        """
            Synchronizes the getting of positions in degrees of all motors specified.
            
            :param motor_ids: specified motor ids [0-253]
            :type motor_ids: list of ids
            :return: list of position in degrees
            
            .. warning:: This method only works with the USB2AX.
            
            """
        motor_models = [self._lazy_get_model(mid) for mid in motor_ids]
        motor_positions = self._send_sync_read_packet(motor_ids, 'PRESENT_POSITION')
        
        return [position_to_degree(pos, model) for model, pos in zip(motor_models, motor_positions)]
    
    def set_sync_positions(self, id_pos_pairs):
        """
            Synchronizes the setting of the specified positions (in degrees) to the motors.
            
            :param id_pos_pairs: position is expressed in degrees.
            :type id_pos_pairs: list of couple (motor id, position)
            
            """
        ids, degrees = zip(*id_pos_pairs)
        motor_models = [self._lazy_get_model(mid) for mid in ids]
        positions = [degree_to_position(rad, model) for rad, model in zip(degrees, motor_models)]
        
        self._send_sync_write_packet('GOAL_POSITION', zip(ids, positions))
    
    
    def get_sync_speeds(self, motor_ids):
        """
            Synchronizes the getting of speed in rpm of all motors specified.
            
            :param motor_ids: specified motor ids [0-253]
            :type motor_ids: list of ids
            :return: list of speed in rpm (positive values correspond to clockwise)
            
            .. warning:: This method only works with the USB2AX.
            
            """
        return map(speed_to_rpm,
                   self._send_sync_read_packet(motor_ids, 'PRESENT_SPEED'))
    
    def set_sync_speeds(self, id_speed_pairs):
        """
            Synchronizes the setting of the specified speeds (in rpm) to the motors.
            
            :param id_speed_pairs: speed is expressed in rpm (positive values correspond to clockwise).
            :type id_speed_pairs: list of couple (motor id, speed)
            
            """
        ids, speeds = zip(*id_speed_pairs)
        speeds = map(rpm_to_speed, speeds)
        self._send_sync_write_packet('MOVING_SPEED', zip(ids, speeds))
    
    
    def get_sync_loads(self, motor_ids):
        """
            Synchronizes the getting of load in percentage of all motors specified.
            
            :param motor_ids: specified motor ids [0-253]
            :type motor_ids: list of ids
            :return: list of load in percentage
            
            .. warning:: This method only works with the USB2AX.
            
            """
        return map(load_to_percent,
                   self._send_sync_read_packet(motor_ids, 'PRESENT_LOAD'))
    
    def set_sync_torque_limits(self, id_torque_pairs):
        """
            Synchronizes the setting of the specified torque limits (in percentage) to the motors.
            
            :param id_speed_pairs: torque limit is expressed as a percentage of the maximum torque.
            :type id_torque_pairs: list of couple (motor id, torque)
            
            """
        ids, torques = zip(*id_torque_pairs)
        torques = map(percent_to_torque_limit, torques)
        
        self._send_sync_write_packet('TORQUE_LIMIT', zip(ids, torques))
    
    
    def get_sync_positions_speeds_loads(self, motor_ids):
        """
            Synchronizes the getting of positions, speeds, load in their respective units of all motors specified.
            
            :param motor_ids: specified motor ids [0-253]
            :type motor_ids: list of ids
            :return: list of (position, speed, load)
            
            .. warning:: This method only works with the USB2AX.
            
            """
        motor_models = [self._lazy_get_model(mid) for mid in motor_ids]
        
        pos, speed, load = zip(*self._send_sync_read_packet(motor_ids, 'PRESENT_POS_SPEED_LOAD'))
        
        pos = [position_to_degree(pos, model) for pos, model in zip(pos, motor_models)]
        speed = map(speed_to_rpm, speed)
        load = map(load_to_percent, load)
        
        return zip(pos, speed, load)
    
    def set_sync_positions_speeds_torque_limits(self, id_pos_speed_torque_tuples):
        """
            Synchronizes the setting of the specified positions, speeds and torque limits (in their respective units) to the motors.
            
            * The position is expressed in degrees.
            * The speed is expressed in rpm (positive values correspond to clockwise).
            * The torque limit is expressed as a percentage of the maximum torque.
            
            :param id_pos_speed_torque_tuples: each value must be expressed in its own units.
            :type id_pos_speed_torque_tuples: list of (motor id, position, speed, torque)
            
            """
        motor_models = [self._lazy_get_model(mid) for mid in motor_ids]
        
        ids, pos, speed, torque = zip(*id_pos_speed_torque_tuples)
        
        pos = [degree_to_position(rad, model) for rad, model in zip(pos, motor_models)]
        speed = map(rpm_to_speed, speed)
        torque = map(percent_to_torque_limit, torque)
        
        self._send_sync_write_packet('GOAL_POS_SPEED_TORQUE', zip(ids, pos, speed, torque))
    
    
    
    # MARK: - Dxl Motor EEPROM Access
    # Those values should not be modified too often !!!
    
    def change_id(self, motor_id, new_motor_id):
        """
            Changes the id of the motor.
            
            Each motor must have a unique id on the bus.
            The range of possible ids is [0, 253].
            
            :param int motor_id: current motor id
            :param int new_motor_id: new motor id
            :raises: ValueError when the id is already taken
            
            """
        if self.ping(new_motor_id):
            raise ValueError('id %d already used' % (new_motor_id))
        
        self._send_write_packet(motor_id, 'ID', new_motor_id)
    
    
    def set_baudrate(self, motor_id, baudrate):
        """
            Changes the baudrate of a specified motor.
            
            :param int motor_id: specified motor id [0-253]
            :param int baudrate: for a list of possible values see http://support.robotis.com/en/product/dxl_main.htm
            
            .. warning:: The baudrate of the motor must match the baudrate of the port. When the baurate is changed for the motor, it will be necessary to open a new port with the new baurate to re-establish communication.
            
            """
        self._send_write_packet(motor_id, 'BAUDRATE', baudrate)
    
    
    def get_return_delay_time(self, motor_id):
        """ 
            Returns the current delay time.
            
            :param int motor_id: specified motor id [0-253]
            :return: return delay time in µs
            
            """
        return self._send_read_packet(motor_id, 'RETURN_DELAY_TIME') * 2
    
    def set_return_delay_time(self, motor_id, return_delay_time):
        """
            Sets a new return delay time in µs.
            
            :param int motor_id: specified motor id [0-253]
            :param int return_delay_time: new return delay time in µs [0-508]
            :raises: ValueError if the return delay time is out of the specified range.
            
            """
        if not (0 <= return_delay_time <= 508):
            raise ValueError('the return delay time must be in [0, 508]')
        
        self._send_write_packet(motor_id, 'RETURN_DELAY_TIME', int(return_delay_time / 2))
    
    
    def get_angle_limits(self, motor_id):
        """
            Gets the lower and upper angle limits of the motor.
            
            .. note:: If you try to set a position to a motor outside of these angle limits, the motor will stop at the limit and an alarm will be triggered.

            :param int motor_id: specified motor id [0-253]
            :return: (lower_limit, upper_limit) in degrees
                        
            """
        return map(lambda pos: position_to_degree(pos, self._lazy_get_model(motor_id)),
                   self._send_read_packet(motor_id, 'ANGLE_LIMITS'))
    
    def set_angle_limits(self, motor_id,
                         lower_limit, upper_limit):
        """
            Sets the lower and upper angle limits of the motor.
            
            .. note:: If you try to set a position to a motor outside of these angle limits, the motor will stop at the limit and an alarm will be triggered.

            :param int motor_id: specified motor id [0-253]
            :param float lower_limit: the lower angle limit
            :param float upper_limit: the upper angle limit
            :raises: ValueError if the lower limit is greater or equal than the upper limit
            
            .. note:: The angle limits must belong to the hardware limits of the motor (e.g. (-150, 150) for an AX-12 motor or (-180, 180) for a MX-28 motor).
                        
            """
        if lower_limit >= upper_limit:
            raise ValueError('The lower limit (%d) must be less than the upper limit (%d).'
                             % (lower_limit, upper_limit))
        
        self._send_write_packet(motor_id,
                                'ANGLE_LIMITS',
                                map(lambda rad: degree_to_position(rad, self._lazy_get_model(motor_id)),
                                    (lower_limit, upper_limit)))
    
    
    # def get_drive_mode(self, motor_id):
    #     motor_model = self._lazy_get_model(motor_id)
    #     if not motor_model.startswith('EX'):
    #         raise DynamixelUnsupportedFunctionForMotorError(self.get_drive_mode,
    #                                                         motor_id,
    #                                                         motor_model)
    
    #     return self._send_read_packet(motor_id, 'DRIVE_MODE')
    
    # def set_drive_mode(self, motor_id, drive_mode):
    #     motor_model = self._lazy_get_model(motor_id)
    #     if not motor_model.startswith('EX'):
    #         raise DynamixelUnsupportedFunctionForMotorError(self.set_drive_mode,
    #                                                         motor_id,
    #                                                         motor_model)
    
    #     self._send_write_packet(motor_id, 'DRIVE_MODE', int(drive_mode))
    
    
    def get_limit_temperature(self, motor_id):
        """
            Returns the motor highest limit of operating temperature.
            
            If the internal temperature of a motor exceeds this limit, it sets
            the Over Heating Error Bit of Status Packet to True and triggers an
            alarm (LED and shutdown).
            
            :param int motor_id: specified motor id [0-253]
            :return: limit of operating temperature in celsius
            
            """
        return self._send_read_packet(motor_id, 'HIGHEST_LIMIT_TEMPERATURE')
    
    def set_limit_temperature(self, motor_id, limit_temperature):
        """
            Sets the highest limit of operating temperature.
            
            :param int motor_id: specified motor id [0-253]
            :param int limit_temperature: new limit of operating temperature [10-99]
            :raises: ValueError if the new temperature is outside of the allowed range
            
            .. warning:: Do not set the temperature lower/higher than the default value. Using the motor when the temperature is high may and can cause damage.
            
            """
        if not (10 <= limit_temperature <= 99):
            raise ValueError('The temperature limit must be in [10,99]')
        
        self._send_write_packet(motor_id, 'HIGHEST_LIMIT_TEMPERATURE', limit_temperature)
    
    
    def get_voltage_limits(self, motor_id):
        """
            Returns the operation range of voltage (V).
            
            If present voltage is out of the range, Voltage Range Error
            of Status Packet is returned as '1' and Alarm is triggered as set
            in the Alarm LED and Alarm Shutdown.
            
            :param int motor_id: specified motor id [0-253]
            :return: (lowest_limit_voltage, highest_limit_voltage) in Volts
            
            """
        return map(lambda v: v * 0.1,
                   self._send_read_packet(motor_id, 'VOLTAGE_LIMITS'))
    
    def set_voltage_limits(self, motor_id,
                           lowest_limit_voltage, highest_limit_voltage):
        """
            Sets the operation voltage range for the motor.
            
            If Present Voltage is out of the range, Voltage Range Error
            of Status Packet is returned as '1' and Alarm is triggered as set
            in the Alarm LED and Alarm Shutdown.
            
            :param int motor_id: specified motor id [0-253]
            :param float lowest_limit_voltage: lowest operating voltage limit [5-25]
            :param float highest_limit_voltage: highest operating voltage limit [5-25]
            :raises: ValueError if the limit are out of range or if the lowest limit is greater than the highest limit
            
            """
        if not (5 <= lowest_limit_voltage <= 25) or not (5 <= highest_limit_voltage <= 25):
            raise ValueError('The lowest limit temperature must be in [5, 25].')
        if (highest_limit_voltage <= lowest_limit_voltage):
            raise ValueError('The highest limit voltage must be superior than the lowest limit voltage.')
        
        self._send_write_packet(motor_id,
                                'VOLTAGE_LIMITS',
                                (lowest_limit_voltage * 10, highest_limit_voltage * 10))
    
    def get_max_torque(self, motor_id):
        """ Returns the maximum output torque avaible in percent. """
        return self._send_read_packet(motor_id, 'MAX_TORQUE') * (100.0 / 1023)
    
    def set_max_torque(self, motor_id, max_torque):
        """ Sets the maximum output torque avaible in percent. """
        if not (0 <= max_torque <= 100):
            raise ValueError('The maximum torque must be in [0, 100].')
        
        self._send_write_packet(motor_id, 'MAX_TORQUE', int(max_torque * 10.23))
    
    
    def get_status_return_level(self, motor_id):
        return self._send_read_packet(motor_id, 'STATUS_RETURN_LEVEL')
    
    def set_status_return_level(self, motor_id, status_return_level):
        # TODO: what happened if we change to 0 or another value ?
        self._send_write_packet(motor_id, 'STATUS_RETURN_LEVEL', status_return_level)
    
    
    def get_alarm_led(self, motor_id):
        return self._send_read_packet(motor_id, 'ALARM_LED')
    
    def set_alarm_led(self, motor_id, alarm_led):
        self._send_write_packet(motor_id, 'ALARM_LED', int(alarm_led))
    
    
    def get_alarm_shutdown(self, motor_id):
        return self._send_read_packet(motor_id, 'ALARM_SHUTDOWN')
    
    def set_alarm_shutdown(self, motor_id, alarm_shutdown):
        self._send_write_packet(motor_id, 'ALARM_SHUTDOWN', int(alarm_shutdown))
    
    
    # MARK: - Dxl Motor RAM Access
    
    def is_torque_enabled(self, motor_id):
        """ Check if the torque is enabled for the specified motor. """
        return bool(self._send_read_packet(motor_id, 'TORQUE_ENABLE'))
    
    def enable_torque(self, motor_id):
        """ Enables the torque to the specified motor. """
        self._set_torque_enable(motor_id, True)
    
    def disable_torque(self, motor_id):
        """ 
            Disables the torque to the specified motor. 
            
            .. note:: When you set a new position to a motor, the torque will be automatically enabled if it was previously disabled.
            
            """
        self._set_torque_enable(motor_id, False)
    
    def _set_torque_enable(self, motor_id, torque_enable):
        self._send_write_packet(motor_id, 'TORQUE_ENABLE', int(torque_enable))
    
    
    def is_led_on(self, motor_id):
        """ Checks if the led is on for the specified motor. """
        return bool(self._send_read_packet(motor_id, 'LED'))
    
    def turn_on_led(self, motor_id):
        """ Turns on the led of the specified motor. """
        self._set_led(motor_id, True)
    
    def turn_off_led(self, motor_id):
        """ Turn off the led of the specified motor. """
        self._set_led(motor_id, False)
    
    def _set_led(self, motor_id, on):
        self._send_write_packet(motor_id, 'LED', int(on))
    
    
    def get_pid_gains(self, motor_id):
        """
            Gets the PID gains for the specified motor.
            
                * The P gain refers to the value of proportional band.
                * The I gain refers to the value of integral action.
                * The D gain refers to the value of derivative action.
            
            Gains values are in between [0-254].
            
            :return: (D gain, I gain, P gain)
            
            .. note:: This method only exists for MX motors. For other kinds of motors, see :py:meth:`get_compliance_margins` and :py:meth:`get_compliance_slopes`
            
            """
        motor_model = self._lazy_get_model(motor_id)
        if not motor_model.startswith('MX'):
            raise DynamixelUnsupportedFunctionForMotorError(self.get_pid_gains,
                                                            motor_id,
                                                            motor_model)
        
        return self._send_read_packet(motor_id, 'GAINS')
    
    def set_pid_gains(self, motor_id,
                      D_gain, I_gain, P_gain):
        """
            Sets the PID gains for the specified motor.
            
            :param int motor_id: specified motor id [0-253]
            :param int D_gain: refers to the value of derivative action [0-254]
            :param int I_gain: refers to the value of integral action [0-254]
            :param int P_gain: refers to the value of proportional band [0-254]

            .. note:: This method only exists for MX motors. For other kinds of motors, see :py:meth:`set_compliance_margins` and :py:meth:`set_compliance_slopes`
            
            """
        motor_model = self._lazy_get_model(motor_id)
        if not motor_model.startswith('MX'):
            raise DynamixelUnsupportedFunctionForMotorError(self.get_pid_gains,
                                                            motor_id,
                                                            motor_model)
        
        self._send_write_packet(motor_id,
                                'GAINS',
                                (derivative_gain, integral_gain, proportional_gain))
    
    
    def get_compliance_margins(self, motor_id):
        """
            Gets the compliance margins of the specified motor.
            
            The compliance margin exists in each direction (CW and CCW) and
            means the error between goal position and present position.
            The greater the value, the more difference occurs.
            
            :param int motor_id: specified motor id [0-253]
            :return: (cw margin, ccw margin)
            
            .. note:: For MX motors, this method has been replaced by :py:meth:`get_pid_gains`.
            
            """
        motor_model = self._lazy_get_model(motor_id)
        if not (motor_model.startswith('AX') or motor_model.startswith('RX')):
            raise DynamixelUnsupportedFunctionForMotorError(self.get_compliance_margins,
                                                            motor_id,
                                                            motor_model)
        
        return self._send_read_packet(motor_id, 'COMPLIANCE_MARGINS')
    
    def set_compliance_margins(self, motor_id,
                               clockwise_margin, counterclockwise_margin):
        """
            Sets new compliance margins for the specified motors.
            
            The compliance margin exists in each direction (CW and CCW) and
            means the error between goal position and present position.
            The greater the value, the more difference occurs.
            
            :param int clockwise_margin: clockwise margin [0-255]
            :param int counterclockwise_margin: counter clockwise margin [0-255]
            
            .. note:: For MX motors, this method has been replaced by :py:meth:`set_pid_gains`.
            
            """
        motor_model = self._lazy_get_model(motor_id)
        if not (motor_model.startswith('AX') or motor_model.startswith('RX')):
            raise DynamixelUnsupportedFunctionForMotorError(self.get_compliance_margins,
                                                            motor_id,
                                                            motor_model)
        self._send_write_packet(motor_id, 'COMPLIANCE_MARGINS',
                                (clockwise_margin, counterclockwise_margin))
    
    def get_compliance_slopes(self, motor_id):
        """
            Gets the compliance slopes of the specified motor.
            
            The compliance slope exists in each direction (CW and CCW) and
            sets the level of torque near the goal position.
            The higher the value, the more flexibility is obtained.
            
            :param int motor_id: specified motor id [0-253]
            :return: (cw slope, ccw slope)
            
            .. note:: For MX motors, this method has been replaced by :py:meth:`get_pid_gains`.
            
            """
        motor_model = self._lazy_get_model(motor_id)
        if not (motor_model.startswith('AX') or motor_model.startswith('RX')):
            raise DynamixelUnsupportedFunctionForMotorError(self.get_compliance_margins,
                                                            motor_id,
                                                            motor_model)
        
        return self._send_read_packet(motor_id, 'COMPLIANCE_SLOPES')
    
    def set_compliance_slopes(self, motor_id,
                              clockwise_slope, counterclockwise_slope):
        """
            Sets new compliance slopes for the specified motors.
            
            The compliance slope exists in each direction (CW and CCW) and
            sets the level of torque near the goal position.
            The higher the value, the more flexibility is obtained.
            
            :param int motor_id: specified motor id [0-253]
            :param int clockwise_slope: clockwise slope [0-255]
            :param int counterclockwise_slope: counter clockwise slope [0-255]
            
            .. note:: For MX motors, this method has been replaced by :py:meth:`set_pid_gains`.

            """
        motor_model = self._lazy_get_model(motor_id)
        if not (motor_model.startswith('AX') or motor_model.startswith('RX')):
            raise DynamixelUnsupportedFunctionForMotorError(self.get_compliance_margins,
                                                            motor_id,
                                                            motor_model)
        
        self._send_write_packet(motor_id, 'COMPLIANCE_SLOPES',
                                (clockwise_slope, counterclockwise_slope))
    
    
    
    def get_position(self, motor_id):
        """ Returns the position in degrees of the specified motor. """
        return position_to_degree(self._send_read_packet(motor_id, 'PRESENT_POSITION'),
                                  self._lazy_get_model(motor_id))
    
    def set_position(self, motor_id, position):
        """ Sets the position in degrees of the specified motor. """
        self._send_write_packet(motor_id, 'GOAL_POSITION',
                                degree_to_position(position,
                                                   self._lazy_get_model(motor_id)))
    
    
    def get_speed(self, motor_id):
        """ Returns the speed in rpm (positive values correspond to clockwise) of the specified motor. """
        return speed_to_rpm(self._send_read_packet(motor_id, 'PRESENT_SPEED'))
    
    def set_speed(self, motor_id, speed):
        """ Sets the speed in rpm (positive values correspond to clockwise) of the specified motor. """
        self._send_write_packet(motor_id, 'MOVING_SPEED',
                                rpm_to_speed(speed))
    
    
    def get_torque_limit(self, motor_id):
        return self._send_read_packet(motor_id, 'TORQUE_LIMIT')
    
    def set_torque_limit(self, motor_id, torque_limit):
        self._send_write_packet(motor_id, 'TORQUE_LIMIT', torque_limit)
    
    
    def get_load(self, motor_id):
        """ Returns the internal load in percent of the specified motor. """
        return load_to_percent(self._send_read_packet(motor_id, 'PRESENT_LOAD'))
    
    
    def get_position_speed_load(self, motor_id):
        """ Returns the position, speed and internal load of the specified motor """
        pos, speed, load = self._send_read_packet(motor_id, 'PRESENT_POS_SPEED_LOAD')
        
        return (position_to_degree(pos, self._lazy_get_model(motor_id)),
                speed_to_rpm(speed),
                load_to_percent(load))
    
    
    def get_voltage(self, motor_id):
        """ Returns the current voltage supplied (in Volt). """
        return self._send_read_packet(motor_id, 'PRESENT_VOLTAGE') * 0.1
    
    
    def get_temperature(self, motor_id):
        """ Returns the internal temperature of the specified motor (in Celsius). """
        return self._send_read_packet(motor_id, 'PRESENT_TEMPERATURE')
    
    
    # def is_registred(self, motor_id): # TODO: ca fait quoi ?
    #     return bool(self._send_read_packet(motor_id, 'REGISTERED'))
    
    
    def is_moving(self, motor_id):
        """ Checks if the motor is moving (whether goal position has been reached). """
        return bool(self._send_read_packet(motor_id, 'MOVING'))
    
    
    def is_eeprom_locked(self, motor_id):
        """ Checks if the EEPROM area can be modified. """
        return bool(self._send_read_packet(motor_id, 'LOCK'))
    
    def lock_eeprom(self, motor_id):
        """ Prevents the modification of the EEPROM area. """
        self._set_eeprom_lock(motor_id, True)
    
    # def unlock_eeprom(self, motor_id):
    #     raise DeprecationWarning('to unlock the eeprom, you should cycle power')
    
    #     self._set_eeprom_lock(motor_id, False)
    
    def _set_eeprom_lock(self, motor_id, lock_eeprom):
        self._send_write_packet(motor_id, 'LOCK', int(lock_eeprom))
    
    
    # def get_punch(self, motor_id):
    #     return self._send_read_packet(motor_id, 'PUNCH')
    
    # def set_punch(self, motor_id, punch):
    #     self._send_write_packet(motor_id, 'PUNCH', punch)
    
    
    # def get_sensed_current(self, motor_id):
    #     motor_model = self._lazy_get_model(motor_id)
    #     if not motor_model.startswith('EX'):
    #         raise DynamixelUnsupportedFunctionForMotorError(self.get_sensed_current,
    #                                                         motor_id,
    #                                                         motor_model)
    #     return self._send_read_packet(motor_id, 'SENSED_CURRENT')
    
    # def get_current(self, motor_id):
    #     motor_model = self._lazy_get_model(motor_id)
    #     if not motor_model in ('MX-64', 'MX-106'):
    #         raise DynamixelUnsupportedFunctionForMotorError(self.get_current,
    #                                                         motor_id,
    #                                                         motor_model)
    #     return self._send_read_packet(motor_id, 'CURRENT')
    
    # def set_current(self, motor_id, current):
    #     motor_model = self._lazy_get_model(motor_id)
    #     if not motor_model in ('MX-64', 'MX-106'):
    #         raise DynamixelUnsupportedFunctionForMotorError(self.get_current,
    #                                                         motor_id,
    #                                                         motor_model)
    #     self._send_write_packet(motor_id, 'CURRENT', current)
    
    
    # MARK: - Low level communication
    
    def _send_packet(self, instruction_packet, wait_for_answer=True):
        with self._lock:
            nbytes = self._serial.write(instruction_packet.to_bytes())
            if nbytes != len(instruction_packet):
                raise DynamixelCommunicationError('Packet not correctly sent',
                                                  instruction_packet,
                                                  None)
            
            if not wait_for_answer:
                return
            
            read_bytes = list(self._serial.read(DynamixelPacketHeader.LENGTH))
            if not read_bytes:
                raise DynamixelTimeoutError(instruction_packet)
            
            try:
                header = DynamixelPacketHeader.from_bytes(read_bytes)
                read_bytes += self._serial.read(header.packet_length)
                status_packet = DynamixelStatusPacket.from_bytes(read_bytes)
                    
            except DynamixelInconsistentPacketError as e:
                raise DynamixelCommunicationError(e.message,
                                                  instruction_packet,
                                                  read_bytes)
        
            if status_packet.error != 0:
                raise DynamixelMotorError(status_packet)
            
            return status_packet
    
    
    def _send_read_packet(self, motor_id, control_name):
        packet = DynamixelReadDataPacket(motor_id, control_name)
        status_packet = self._send_packet(packet)
                
        return self._decode_data(status_packet.parameters,
                                 REG_SIZE(control_name))
    
    
    def _send_sync_read_packet(self, motor_ids, control_name):        
        packet = DynamixelSyncReadDataPacket(motor_ids, control_name)
        status_packet = self._send_packet(packet)

        answer = reshape_list(status_packet.parameters, REG_LENGTH(control_name))

        return map(lambda data: self._decode_data(data, REG_SIZE(control_name)),
                   answer)
    
    
    def _send_write_packet(self, motor_id, control_name, data):
        data = self._code_data(data, REG_SIZE(control_name))
        
        write_packet = DynamixelWriteDataPacket(motor_id, control_name, data)        
        self._send_packet(write_packet)
    
    def _send_sync_write_packet(self, control_name, data_tuples):
        code_func = lambda chunk: [chunk[0]] + self._code_data(chunk[1:], REG_SIZE(control_name))
        data = flatten_list(map(code_func, data_tuples))
        
        sync_write_packet = DynamixelSyncWriteDataPacket(control_name, data)
        self._send_packet(sync_write_packet, wait_for_answer=False)
    
    
    def _build_packet(self, motor_id, instruction_name, parameters=()):
        instruction = DXL_INSTRUCTIONS[instruction_name]
        
        length = len(parameters) + 2
        checksum = self._compute_checksum(motor_id, length, instruction, parameters)
        
        packet = [0xFF, 0xFF, motor_id, length, instruction] + list(parameters) + [checksum]
        
        return packet
    
    
    def _compute_checksum(self, motor_id, length, instruction, parameters):
        # Check Sum = ~ ( ID + Length + Instruction + Parameter1 + ... + Parameter N)
        return 255 - ((motor_id + length + instruction + sum(parameters)) % 256)
    
    
    # MARK : - Data coding/uncoding
    
    def _code_data(self, data, data_length):
        if data_length not in (1, 2):
            raise ValueError('Unsupported size of data (%d)' % (data_length))
        
        if not hasattr(data, '__len__'):
            data = [data]
        
        if data_length == 2:
            data = flatten_list(map(integer_to_two_bytes, data))
        
        return list(data)
    
    def _decode_data(self, data, data_length):
        if data_length not in (1, 2):
            raise ValueError('Unsupported size of data (%d)' % (data_length))
        
        if data_length == 2:
            data = map(two_bytes_to_integer, reshape_list(data, 2))
        return data if len(data) > 1 else data[0]


# MARK: - Dxl Error

class DynamixelCommunicationError(Exception):
    def __init__(self, message, instruction_packet, response):        
        self.message = message
        self.instruction_packet = instruction_packet
        self.response = map(ord, response) if response else None
    
    def __str__(self):
        return '%s (instruction packet: %s, status packet: %s)' \
            % (self.message, self.instruction_packet, self.response)

class DynamixelTimeoutError(DynamixelCommunicationError):
    def __init__(self, instruction_packet):
        DynamixelCommunicationError.__init__(self, 'Timeout', instruction_packet, None)


class DynamixelMotorError(Exception):
    def __init__(self, motor_id, error_code):
        self.motor_id = motor_id
        self.error_code = error_code
    
    def __str__(self):
        return 'Motor %d returned error code %d' % (self.motor_id, self.error_code)

class DynamixelUnsupportedMotorError(Exception):
    def __init__(self, motor_id, model_number):
        self.motor_id = motor_id
        self.model_number = model_number
    
    def __str__(self):
        return 'Unsupported Motor with id: %d and model number: %d' % (self.motor_id, self.model_number)

class DynamixelUnsupportedFunctionForMotorError(Exception):
    def __init__(self, func, motor_id, motor_model):
        self.func = func
        self.motor_id = motor_id
        self.motor_model = motor_model
    
    def __str__(self):
        return 'Unsupported function (%s) for motor (%d: %s)' % (self.func.__name__, self.motor_id, self.motor_model)
