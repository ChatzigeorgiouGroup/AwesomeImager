#!/usr/bin/env python2
# -*- coding: utf-8 -*-
"""
Created on March 29 2018

@author: kushal, daniel

Chatzigeorgiou Group
Sars International Centre for Marine Molecular Biology

GNU GENERAL PUBLIC LICENSE Version 3, 29 June 2007

Just a bunch of generalized classed for image acquisition, preview, and writing from Hamamatsu and OpenCV cameras.

"""

from __future__ import print_function
import sys
import hamamatsu_camera as hc
import abc
import threading
import Queue
import cv2
import numpy as np
import pyqtgraph as pg
import time
import datetime
import json
import tifffile
import multiprocessing


class BaseCamera(threading.Thread):
    """Base class for all types of cameras"""
    __metaclass__ = abc.ABCMeta

    def __init__(self):
        threading.Thread.__init__(self)

    @abc.abstractmethod
    def run(self):
        pass

    @abc.abstractmethod
    def end(self):
        pass

    @property
    def framerate(self):
        return self._framerate

    @framerate.setter
    def framerate(self, framerate):
        self._framerate = framerate


class BasePreview:
    """Base class to display a preview of a frame from the camera using the ImageView class from pyqtgraph"""
    def __init__(self):
        self.iv = pg.imageview.ImageView()

        colors = [
            (0, 0, 0),
            (7, 0, 220),
            (236, 0, 134),
            (246, 246, 0),
            (255, 255, 255),
            (0, 255, 0)
        ]

        cmap = pg.ColorMap(pos=np.linspace(0.0, 1.0, 6), color=colors)
        self.iv.setColorMap(cmap)
        self.iv.setLevels(0, 65535)
        self.hist = self.iv.getHistogramWidget()
        self.hist.vb.enableAutoRange(self.hist.vb.YAxis, False)
        self.iv.show()

    def update_preview(self, img):
        self.iv.setImage(img, autoRange=False, autoLevels=False, autoHistogramRange=False)


class BaseWriter(threading.Thread):
    """Base class that contains methods for converting 16 bit to 8 bit grey-scale frames and saving metadata"""
    def __init__(self, threading_queue, filename, compression_level=1, levels=(0, 65535), metadata={}):
        threading.Thread.__init__(self)
        assert isinstance(threading_queue, Queue.Queue)
        self.q = threading_queue
        self.filename = filename
        self.comp_lev = compression_level
        self.levels = levels
        self.metadata = metadata
        if 'framerate' not in self.metadata.keys():
            try:
                self.metadata['framerate'] = 1/self.metadata['exposure']
            except KeyError:
                raise KeyError('Exposure or Framerate must be specified to save as metadata')

        self.tiff_writer = tifffile.TiffWriter(filename, bigtiff=True, append=True)

    @abc.abstractmethod
    def run(self):
        pass

    @abc.abstractmethod
    def end(self):
        pass

    def _lut_8bit(self, image):
        image.clip(self.levels[0], self.levels[1], out=image)
        image -= self.levels[0]
        np.floor_divide(image, (self.levels[1] - self.levels[0] + 1) / 256,
                        out=image, casting='unsafe')
        return image.astype(np.uint8)

    def convert_to_8bit(self, image):
        lut = np.arange(65536, dtype=np.uint16)
        lut = self._lut_8bit(lut)
        return np.take(lut, image).astype(np.uint8)

    def save_metadata(self, filename):
        date = datetime.datetime.fromtimestamp(time.time())
        ymd = date.strftime('%Y%m%d')
        hms = date.strftime('%H%M%S')

        meta = {'framerate':    self.metadata['framerate'],
                'source':       'AwesomeImager',
                'version':      self.metadata['version'],
                'date':         ymd,
                'time':         hms,
                'stims':        self.metadata['stims'],
                'level_min':    int(self.metadata['levels'][0]),
                'level_max':    int(self.metadata['levels'][1])}

        if filename.endswith('.tiff'):
            json_file = filename[:-5] + '.json'
        elif filename.endswith('.tif'):
            json_file = filename[:-4] + '.json'
        else:
            raise ValueError
        with open(json_file, 'w') as f:
            json.dump(meta, f)


class BaseHamamatsu(BaseCamera):
    """Base class for Hamamatsu cameras"""
    __metaclass__ = abc.ABCMeta

    def __init__(self, **kwargs):
        BaseCamera.__init__(self)
        if 'exposure' not in kwargs.keys():
            raise KeyError('No exposure value specified')

        self.hcam = hc.HamamatsuCameraMR(0)
        self.camera_open = True
        # Set camera parameters.
        cam_offset = 100
        cam_x = 2048
        cam_y = 2048
        self.hcam.setPropertyValue("defect_correct_mode", "OFF")
        self.hcam.setPropertyValue("subarray_hsize", cam_x)
        self.hcam.setPropertyValue("subarray_vsize", cam_y)
        self.hcam.setPropertyValue("binning", "1x1")
        self.hcam.setPropertyValue("readout_speed", 2)
        self.exposure = kwargs['exposure']

    @property
    def exposure(self):
        e = self.hcam.getPropertyValue("exposure_time")
        return e

    @exposure.setter
    def exposure(self, e):
        self.hcam.setPropertyValue("exposure_time", e)
        self.framerate = 1/e

    def get_grey_values(self):
        """
        :rtype: np.ndarray
        :return: 1D numpy array of grey values
        """

        [frame, dim] = self.hcam.getFrames()
        grey_values = frame[0].getData()
        return grey_values

    @abc.abstractmethod
    def end(self):
        self.hcam.stopAcquisition()
        self.hcam.shutdown()
        self.camera_open = False


class BaseOpenCV(BaseCamera):
    """
    Adapted from Daniel Dondorp
    Base class for OpenCV compatible cameras
    """
    __metaclass__ = abc.ABCMeta

    def __init__(self, camera=0, framerate=30, shape=(7680, 4320), exposure=-5.0):
        """
        :param camera: Which connected camera to use.
        :param framerate:
        :param shape:
        :param brightness:
        :param exposure: negative values, get evaluates  as 2**param_val by camera.
        """
        BaseCamera.__init__(self)
        self.camera = camera
        self.cap = cv2.VideoCapture(self.camera)
        self.alive = False
        self.framerate = framerate
        self.shape = shape

        self.aperture = self.cap.get(cv2.CAP_PROP_APERTURE)
        self.exposure = exposure

    @property
    def framerate(self):
        return self._framerate

    @framerate.setter
    def framerate(self, framerate):
        self._framerate = framerate
        self.cap.set(cv2.CAP_PROP_FPS, framerate)

    @property
    def shape(self):
        return self._shape

    @shape.setter
    def shape(self, shape):
        self._shape = shape
        w, h = shape
        self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, w)
        self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, h)
        # print("Video shape set to : ", w, h)

    @property
    def exposure(self):
        return self._exposure

    @exposure.setter
    def exposure(self, exposure):
        self._exposure = exposure
        self.cap.set(cv2.CAP_PROP_EXPOSURE, exposure)

    def reconnect(self):
        self.cap.release()
        self.cap = cv2.VideoCapture(self.camera)

    def read(self):
        ret = False
        while ret == False:
            ret, frame = self.cap.read()

        return cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)

    def end(self):
        self.cap.release()


class PreviewOpenCV(BaseOpenCV, BasePreview):
    """
    Adapted from Daniel Dondorp
    """
    def __init__(self):
        BaseOpenCV.__init__(self)
        BasePreview.__init__(self)
        super(PreviewOpenCV, self).__init__(BasePreview)

    def run(self):
        print("Preview Starting")
        self.alive = True

        frames = 0
        start_time = time.time()

        while self._alive:

            ret, frame = self.cap.read()

            if ret:
                self.update_preview(frame)

            frames += 1
            if frames == 10:
                end_time = time.time()
                total_time = (end_time - start_time)
                fps = frames / total_time
                sys.stdout.write("\r fps = " + str(fps) + " time for 10 frames: " + str(total_time))
                frames = 0
                start_time = time.time()
        self.iv.close()
        super(PreviewOpenCV, self).end()

    def end(self):
        self._alive = False


class AcquireOpenCV(BaseOpenCV):
    """
    Adapted from Daniel Dondorp
    """
    def __init__(self):
        BaseOpenCV.__init__(self)

    def run(self, name="out", duration=60):
        w = int(self.cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        h = int(self.cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        framerate = self.framerate

        savepath = name + ".avi"

        fourcc = cv2.VideoWriter_fourcc(*"XVID")
        out = cv2.VideoWriter(savepath, fourcc, framerate, (w, h), isColor=False)

        start_time = time.time()
        end_time = start_time
        self.alive = True
        fc = 0
        while (end_time - start_time) < duration and self.alive:

            ret, frame = self.cap.read()

            if ret:
                frame = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
                out.write(frame)
                fc += 1
                cv2.imshow('Recording', frame)

            end_time = time.time()
            sys.stdout.write("\r Recording! " + str(np.round((end_time - start_time), 2)) + "/" + str(
                duration) + " seconds       ")

        else:
            sys.stdout.write("\n Recording Complete! Saved as " + name + "Framecount: " + str(fc))

            cv2.destroyWindow("Recording")
            out.release()
            self.alive = False


class PreviewHamamatsu(BaseHamamatsu, BasePreview):
    def __init__(self, **kwargs):
        BaseHamamatsu.__init__(self, **kwargs)
        # super(BaseHamamatsu, self).__init__()
        BasePreview.__init__(self)
        self._show_preview = True

    def run(self):
        self.hcam.startAcquisition()
        first_img = True
        while self._show_preview:
            try:
                img = np.reshape(self.get_grey_values(), (2048, 2048))
                self.iv.setImage(img, autoRange=False, autoLevels=False, autoHistogramRange=False)

                if first_img:
                    self.iv.autoLevels()
                    first_img = False
                self.levels = self.hist.getLevels()

            except Exception as e:
                print(e)

        super(PreviewHamamatsu, self).end()

    def end(self):
        self._show_preview = False
        self.iv.close()


class AcquireHamamatsu(BaseHamamatsu):
    def __init__(self, parameters, threading_queue, duration):
        kwargs = parameters
        BaseHamamatsu.__init__(self, **kwargs)
        self.q = threading_queue
        self.duration = duration
        self._acquire = True

    def run(self):
        frame_num = 0
        self.hcam.startAcquisition()
        self.stop_time = time.time() + self.duration
        while (time.time() < self.stop_time) and self._acquire:
            try:
                # self.lens.focalpower( <<appropriate focal power>> )

                # << Modulo calculation to find the next focal power to adjust to according to focal_interval >>

                # time.sleep(0.014) # Wait 14ms for the lens to adjust to the right focal power

                # <<** might be possible to lower this time because of small stack intervals
                # and use larger wait times only when beginning the next stack!! ** >>

                # [frame, dim] = self.hcam.getFrames()
                # grey_values = frame[0].getData()

                self.q.put(self.get_grey_values())
                print('Read frame num ' + str(frame_num))
                frame_num += 1

            except KeyboardInterrupt:
                self.end()
                raise KeyboardInterrupt

            except Exception as e:
                print('Something went wrong during acquisition of frame num: ' + str(frame_num) + '\n' + str(e))

        self.q.put('done')
        super(AcquireHamamatsu, self).end()

    def end(self):
        self._acquire = False


# TODO: TRY MULTIPROCESSING HERE SINCE I'M ONLY SENDING A NUMPY ARRAY AND NOT HCAM OBJECT !!!!!!!!!!
class WriterHamamatsu(BaseWriter, BasePreview):
    """Use for Hamamatsu Cameras"""

    def __init__(self, threading_queue, filename, compression_level, levels, metadata):
        BaseWriter.__init__(self, threading_queue=threading_queue, filename=filename,
                            compression_level=compression_level, levels=levels, metadata=metadata)
        BasePreview.__init__(self)
        self.iv.setLevels(levels[0], levels[1])
        self.write = True

    def run(self):
        img_num = 0
        while self.write:
            if not self.q.not_empty:
                continue

            cam_data = self.q.get()

            if type(cam_data) is str:
                if cam_data == 'done':
                    break

            else:
                try:
                    img = np.reshape(cam_data, (2048, 2048))
                    try:
                        self.iv.setImage(img, autoRange=False, autoLevels=False, autoHistogramRange=False)
                    except Exception as e:
                        print('Error displaying an image: ' + str(e))

                    img = self.convert_to_8bit(img)

                    self.tiff_writer.save(img, compress=self.comp_lev)
                    print('qsize is: ' + str(self.q.qsize()))
                    print('wrote ImgNum: ' + str(img_num))
                    # self.parent.set_frames_written_progressBar(self.imgNum, self.q.qsize())

                    self.q.task_done()
                    img_num += 1

                except KeyboardInterrupt:
                    break
                    
        self.tiff_writer.close()
        self.save_metadata(self.filename)

    def end(self):
        self.write = False
