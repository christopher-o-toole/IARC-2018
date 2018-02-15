#!/usr/bin/env python2.7
############################################
# Multirotor Robot Design Team
# Missouri University of Science and Technology
# Spring 2018
# Christopher O'Toole

from AutonomousFlight import FlightVector
from ATC import Tower
import ATC
import cv2
import numpy as np
import time
import sys
import threading
import multiprocessing
import pygame
from sklearn.preprocessing import normalize

import pygazebo
import pygazebo.msg.image_stamped_pb2
import logging
import argparse

import trollius
from trollius import From
from timeit import default_timer as timer
from ardupilot_gazebo_monitor import ArdupilotGazeboMonitor, QuadcopterCrash, READY_TO_FLY_TIMEOUT

logging.basicConfig()

DEFAULT_TIMEOUT = .001

# displays a live stream of footage from the drone in the simulator, set to False for slightly better runtime performance
_DEBUG = True

class ResetSimulatorException(Exception):
    pass

class ArdupilotDisconnect(Exception):
    pass

class SimpleDroneAI():
    # reset event location
    RESET_EVENT_LOCATION = '/gazebo/default/reset'
    # gazebo reset complete location
    RESET_COMPLETE_EVENT_LOCATION = '/gazebo/default/reset_complete'
    # reset event type
    RESET_EVENT_TYPE = 'gazebo.msgs.GzString'
    # color camage image message location
    COLOR_CAMERA_MSG_LOCATION = '/gazebo/default/realsense_camera/rs/stream/color'
    # depth camera image message location
    DEPTH_CAMERA_MSG_LOCATION = '/gazebo/default/realsense_camera/rs/stream/depth'
    # depth far clip
    DEPTH_FAR_CLIP_MM = 10000
    # depth near clip
    DEPTH_NEAR_CLIP_MM = 300
    # camera message type
    CAMERA_MSG_TYPE = 'gazebo.msgs.ImageStamped'
    # altitude to hover at after takeoff in meters
    TAKEOFF_HEIGHT = 2
    # maximum time spent on drone shutdown task
    DRONE_SHUTDOWN_TASK_TIMEOUT = 7
    # max speed (m/s)
    CRUISING_SPEED = 1

    def __init__(self, ardupilot_connection):
        self._tower = Tower()
        self._tower.initialize()
        self._ardupilot_connection = ardupilot_connection

        self._lock = threading.Lock()
        self._last_image_retrieved = None
        self._reset_ai = False

    
    def _update(self, img):
        if _DEBUG:
            bgr_img = cv2.cvtColor(img, cv2.COLOR_RGB2BGR)
            cv2.imshow('debug', bgr_img)
            cv2.waitKey(1)

        return self._ardupilot_connection.update()

    def _depth_image_handler(self, data):
        message = pygazebo.msg.image_stamped_pb2.ImageStamped.FromString(data)
        h, w = (message.image.height, message.image.width)
        img = np.fromstring(message.image.data, dtype=np.uint16).reshape(h, w)
        maximum = np.ones((h, w), dtype=np.float32)*SimpleDroneAI.DEPTH_COLOR_SCALE_MAX

        if _DEBUG:
            norm = (img.astype(np.float32)-img.min())/(img.max()-img.min())*255
            gray = norm.astype(np.uint8)
            color = cv2.applyColorMap(gray, cv2.COLORMAP_AUTUMN)
            cv2.imshow('depth', color)
            cv2.waitKey(1)

    def _event_handler(self, data):
        message = pygazebo.msg.image_stamped_pb2.ImageStamped.FromString(data)
        h, w = (message.image.height, message.image.width)
        img = np.fromstring(message.image.data, dtype=np.uint8).reshape(h, w, 3)

        with self._lock:
            self._last_image_retrieved = img

    def _reset(self, data):
        self._reset_ai = True

    def _failsafe_drone_shutdown(self):
        tasks = []

        for task in [self._tower.land, self._tower.disarm_drone, self._tower.shutdown]:
            tasks.append(threading.Thread(target=task))
            tasks[-1].start()
            tasks[-1].join(timeout=SimpleDroneAI.DRONE_SHUTDOWN_TASK_TIMEOUT)

        if any([task.is_alive() for task in tasks]):
            ardupilot_connection.stop(force=True)

    def _reset_complete(self, publisher):
        print('Reset command receieved. Resetting AI...')
        self._failsafe_drone_shutdown()

    @trollius.coroutine
    def run(self):
        manager = yield From(pygazebo.connect()) 
        publisher = yield From(manager.advertise(SimpleDroneAI.RESET_COMPLETE_EVENT_LOCATION, SimpleDroneAI.RESET_EVENT_TYPE))

        subscriber = manager.subscribe(SimpleDroneAI.COLOR_CAMERA_MSG_LOCATION, SimpleDroneAI.CAMERA_MSG_TYPE, self._event_handler)
        depth_image_subscriber = manager.subscribe(SimpleDroneAI.DEPTH_CAMERA_MSG_LOCATION, SimpleDroneAI.CAMERA_MSG_TYPE, self._depth_image_handler)
        reset_event = manager.subscribe(SimpleDroneAI.RESET_EVENT_LOCATION, SimpleDroneAI.RESET_EVENT_TYPE, self._reset)

        try:
            yield From(subscriber.wait_for_connection())
            self._tower.takeoff(SimpleDroneAI.TAKEOFF_HEIGHT)

            while True:
                if self._reset_ai:
                    raise ResetSimulatorException()
                if self._last_image_retrieved is not None and self._lock.acquire(False):
                    try:
                        if not self._update(self._last_image_retrieved):
                            raise ArdupilotDisconnect()
                    finally:
                        self._last_image_retrieved = None
                        self._lock.release()

                yield From(trollius.sleep(0.01))
        except (ResetSimulatorException, QuadcopterCrash, ArdupilotDisconnect) as e:
            if isinstance(e, QuadcopterCrash):
                print('Quadcopter crash detected! Interpreting as a cue to reset the simulator...')
        
            exc_type, value, traceback = sys.exc_info()

            self._reset_complete(publisher)

            message = pygazebo.msg.gz_string_pb2.GzString()
            message.data = 'crash' if isinstance(e, (QuadcopterCrash, ArdupilotDisconnect)) else 'ack' 
            
            yield From(publisher.publish(message))
            yield From(trollius.sleep(1))

            raise exc_type, value, traceback    
        finally:
            cv2.destroyAllWindows()

parser = argparse.ArgumentParser(description='Processes AI settings passed in')
parser.add_argument('--test', '-T', type=int, help='test experimental features', default=1)
args = parser.parse_args()

if not args.test:
    with ArdupilotGazeboMonitor(speedup=3) as ardupilot_connection:
        while True:
            try:
                while not ardupilot_connection.is_ready() or not ardupilot_connection.wait_for_ready_to_fly(timeout=DEFAULT_TIMEOUT):
                    start = timer()

                    while ardupilot_connection.update() and not (start-timer()) >= READY_TO_FLY_TIMEOUT:
                        if ardupilot_connection.wait_for_ready_to_fly(timeout=DEFAULT_TIMEOUT):
                            break
                
                    if not ardupilot_connection.is_ready() or not ardupilot_connection.wait_for_ready_to_fly(timeout=DEFAULT_TIMEOUT):
                        ardupilot_connection.restart()

                ai = SimpleDroneAI(ardupilot_connection)
                loop = trollius.get_event_loop()
                loop.run_until_complete(ai.run())
            except QuadcopterCrash:
                ardupilot_connection.flush()
                ardupilot_connection.reset()
                ardupilot_connection.restart()
            except ResetSimulatorException:
                if ardupilot_connection.wait_for_gps_glitch(timeout=3):
                    ardupilot_connection.reset()
            except ArdupilotDisconnect:
                ardupilot_connection.restart()
            except KeyboardInterrupt:
                break
else:
    def _depth_image_handler(data):
        message = pygazebo.msg.image_stamped_pb2.ImageStamped.FromString(data)
        h, w = (message.image.height, message.image.width)
        img = np.fromstring(message.image.data, dtype=np.uint16).reshape(h, w)
        zeros = np.zeros((h, w), dtype=np.uint16)
        
        if _DEBUG:
            scale = (SimpleDroneAI.DEPTH_FAR_CLIP_MM-SimpleDroneAI.DEPTH_NEAR_CLIP_MM)
            offset = SimpleDroneAI.DEPTH_NEAR_CLIP_MM
            norm = np.maximum(img.astype(np.float32)-offset, zeros)/scale * 255
            gray = norm.astype(np.uint8)
            color = cv2.applyColorMap(gray, cv2.COLORMAP_AUTUMN)
            cv2.imshow('depth', color)
            cv2.waitKey(1)

    @trollius.coroutine
    def run():
        manager = yield From(pygazebo.connect())
        depth_image_subscriber = manager.subscribe(SimpleDroneAI.DEPTH_CAMERA_MSG_LOCATION, SimpleDroneAI.CAMERA_MSG_TYPE, _depth_image_handler)

        try:
            yield From(depth_image_subscriber.wait_for_connection())

            while True:
                yield From(trollius.sleep(0.01))
        except KeyboardInterrupt as e:
            pass
        finally:
            cv2.destroyAllWindows()

    loop = trollius.get_event_loop()
    loop.run_until_complete(run())