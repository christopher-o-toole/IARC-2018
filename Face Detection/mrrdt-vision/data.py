############################################
# Multi-Rotor Robot Design Team
# Missouri University of Science and Technology
# Summer 2017
# Christopher O'Toole

"""
A small library dedicated to making the creation of large object detection datasets easy.

Notes
----------
Currently a large portion of these functions are somewhat closely coupled with the model described in [1], 
but enough infrastructure is in place for one to easily generalize these functions for use with any object
detection model. In particular, parameters to specify the dimensions to resize each image to in place of the 
stage index system and a modification to the mine_negatives function to allow a detection function to be passed
in to it would increase code reusability significantly.

References
----------
.. [1] Li, H., Lin, Z., Shen, X., Brandt, J., & Hua, G. (2015). A convolutional neural network cascade for face detection. 
       In Proceedings of the IEEE Conference on Computer Vision and Pattern Recognition (pp. 5325-5334).
"""

from __future__ import absolute_import, division, print_function, unicode_literals
__metaclass__ = type

import random
import os
import math

import h5py
import cv2
import numpy as np
import sqlite3

from .util import static_vars
from .persistence import GlobalStorage
from .main import DEBUG

# folder containing the SQL database for object annotations
POSITIVE_IMAGE_DATABASE_FOLDER = 'aflw/data/'
# folder containing positive images
POSITIVE_IMAGE_FOLDER = POSITIVE_IMAGE_DATABASE_FOLDER + 'flickr/'
# SQL database file containing the object annotations
POSITIVE_IMAGE_DATABASE_FILE = os.path.join(POSITIVE_IMAGE_DATABASE_FOLDER, 'aflw.sqlite')
# path to each classifier's positive dataset file
OBJECT_DATABASE_PATHS = ('data/pos12.hdf', 'data/pos24.hdf', 'data/pos48.hdf')
# path to folder which contains files that list test image locations
TEST_IMAGE_DATABASE_FOLDER = 'Annotated Faces in the Wild/FDDB-folds/'
# base folder for test images
TEST_IMAGES_FOLDER = 'Annotated Faces in the Wild/originalPics'

# label for the dataset in the h5py file.
DATASET_LABEL = 'data'
# label for the class labels in the h5py file when presnet.
LABELS_LABEL = 'labels'
# size of chunks to split h5py file data into
CHUNK_SIZE = 256

# folder containing negative images
NEGATIVE_IMAGE_FOLDER = 'Negative Images/images/'
# path to each classifier's negative dataset file
NEGATIVE_DATABASE_PATHS = ('data/neg12.hdf', 'data/neg24.hdf', 'data/neg48.hdf')
# default maximum number of negatives to attempt to retrieve per image
TARGET_NUM_NEGATIVES_PER_IMG = 40
# default maximum number of negatives to attempt to retrieve from all available negative images.
TARGET_NUM_NEGATIVES = 300000
# minimum square region for which the detector should still be effective
MIN_OBJECT_SCALE = 80
# relative offset to move sliding window with at each step.
OFFSET = 4

# primary input scales for every stage of the cascade
SCALES = ((12, 12), (24, 24), (48, 48))

# path to each calibrator's calibration dataset file
CALIBRATION_DATABASE_PATHS = {SCALES[0][0]:'data/calib12.hdf', SCALES[1][0]:'data/calib24.hdf', SCALES[2][0]:'data/calib48.hdf'}
# maximum number of calibration samples to attempt to retrieve from all available object annotations
TARGET_NUM_CALIBRATION_SAMPLES = 337500
# bounding box scale transformations
SN = (.83, .91, 1, 1.1, 1.21)
# bounding box x transformations
XN = (-.17, 0, .17)
# bounding box y transformations
YN = XN
# list of transformations formed by all combinations of values in SN, XN, and YN
CALIB_PATTERNS = [(sn, xn, yn) for sn in SN for xn in XN for yn in YN]
# same as CALIB_PATTERNS, but the data is placed in a numpy array instead of standard python collection types.
CALIB_PATTERNS_ARR = np.asarray(CALIB_PATTERNS)

# fixed random seed for reproducibility
RANDOM_SEED = 42
np.random.seed(RANDOM_SEED)

def num_detection_windows_along_axis(size, stage_idx=0):
    """
    Get the maximum number of detection windows that can fit along an axis with length `size` for the given cascade
    stage.

    Parameters
    ----------
    size: int
        Axis length.
    stage_idx: int, optional
        Cascade stage index, default is 0.

    Returns
    -------
    out: int
    Returns maximum number of detection windows that can fit along the given axis for the cascade stage specified.
    """
    
    return (size-SCALES[stage_idx][0])//OFFSET+1

@static_vars(faces=[])
def get_face_annotations(db_path=POSITIVE_IMAGE_DATABASE_FILE, pos_img_folder=POSITIVE_IMAGE_FOLDER):
    """
    Retrieve face annotation data from an SQL database.

    Parameters
    ----------
    db_path: str, optional
        Path to the SQL database file, defaults to `POSITIVE_IMAGE_DATABASE_FILE`.
    pos_img_folder: str, optional
        Path to the folder which contains the images referenced by the SQL database, defaults to `POSITIVE_IMAGE_FOLDER`.

    Returns
    -------
    out: list
    Returns a list with entries of the form (<image path>, <x>, <y>, <width>, <height>)
    """

    if len(get_face_annotations.faces) == 0:
        with sqlite3.connect(db_path) as conn:
            c = conn.cursor()

            select_string = "faceimages.filepath, facerect.x, facerect.y, facerect.w, facerect.h"
            from_string = "faceimages, faces, facepose, facerect"
            where_string = "faces.face_id = facepose.face_id and faces.file_id = faceimages.file_id and faces.face_id = facerect.face_id"
            query_string = "SELECT " + select_string + " FROM " + from_string + " WHERE " + where_string

            for row in c.execute(query_string):
                img_path = os.path.join(pos_img_folder, str(row[0]))

                if os.path.isfile(img_path):
                    get_face_annotations.faces.append((img_path,) + (row[1:]))

    return get_face_annotations.faces

def squash_coords(img, x, y, w, h):
    """
    Squash coordinates to fit within the space of the given image

    Parameters
    ----------
    img: numpy.ndarray
        Image to squash coordinates relative to. Returned coordinates will always be within the boundaries of this image.
    x: int
        x coordinate for the region's top-left corner.
    y: int
        y coordinate for the region's top-left corner.
    w: int
        width of the region
    h: int
        height of the region
    
    Returns
    -------
    out: tuple
    Returns the squashed coordinates in a tuple of the form (x, y, w, h)
    """

    y = min(max(0, y), img.shape[0])
    x = min(max(0, x), img.shape[1])
    h = min(img.shape[0]-y, h)
    w = min(img.shape[1]-x, w)
    return (x, y, w, h)

def create_positive_dataset(stage_idx, get_object_annotations=get_face_annotations, pos_img_folder=POSITIVE_IMAGE_FOLDER):
    """
    Creates a positive dataset file from a set of image annotations.

    Parameters
    ----------
    stage_idx: int
        Cascade stage index.
    get_object_annotations: callable, optional
        Callable that returns a sequence of image annotations with entries in the form
        (<image path>, <x>, <y>, <width>, <height>), defaults to `data.get_face_annotations`. 
    pos_img_folder: str, optional
        Path to the folder which contains the images referenced by the image annotation sequence, defaults to `POSITIVE_IMAGE_FOLDER`.
    
    Notes
    ----------
    If this function succeeds, a file will be creeated in the current working directory with the name `OBJECT_DATABASE_PATHS[stage_idx]`.

    Returns
    -------
    None
    """

    file_name = OBJECT_DATABASE_PATHS[stage_idx]
    resize_to = SCALES[stage_idx]
    object_annotations = get_object_annotations(pos_img_folder=pos_img_folder)
    images = np.zeros((len(object_annotations), resize_to[1], resize_to[0], 3), dtype=np.uint8)
    cur_img = None
    prev_img_path = None

    for i, (img_path, x, y, w, h) in enumerate(object_annotations):
        if img_path != prev_img_path:
            cur_img = cv2.imread(img_path)

        x, y, w, h = squash_coords(cur_img, x, y, w, h)
        images[i] = cv2.resize(cur_img[y:y+h,x:x+w], resize_to)
        prev_img_path = img_path

    with h5py.File(file_name, 'w') as out:
        out.create_dataset(DATASET_LABEL, data=images, chunks=(CHUNK_SIZE,) + (images.shape[1:]))

def create_negative_dataset(stage_idx, neg_img_folder=NEGATIVE_IMAGE_FOLDER, num_negatives=TARGET_NUM_NEGATIVES, num_negatives_per_img=TARGET_NUM_NEGATIVES_PER_IMG):
    """
    Creates a negative dataset file from a set of non-object images.

    Parameters
    ----------
    stage_idx: int
        Cascade stage index.
    neg_img_folder: str, optional
        Path to the folder which contains the non-object images, defaults to `NEGATIVE_IMAGE_FOLDER`.
    num_negatives: int, optional
        Maximum number of negative samples to retrieve, defaults to `TARGET_NUM_NEGATIVES`.
    num_negatives_per_img: int, optional
        Maximum number of negative samples to retrieve from a single image, defaults to 'TARGET_NUM_NEGATIVES_PER_IMG`.
    
    Notes
    ----------
    If this function succeeds, a file will be created in the current working directory with the name `NEGATIVE_DATABASE_PATHS[stage_idx]`.
    
    Returns
    -------
    None
    """

    file_name = NEGATIVE_DATABASE_PATHS[stage_idx]
    resize_to = SCALES[stage_idx]
    negative_image_paths = [os.path.join(neg_img_folder, file_name) for file_name in os.listdir(neg_img_folder)]
    images = np.zeros((num_negatives, resize_to[1], resize_to[0], 3), dtype=np.uint8)
    neg_idx = 0
    num_negatives_retrieved_from_img = 0

    for i in np.random.permutation(len(negative_image_paths)):
        if neg_idx >= num_negatives: break
        img = cv2.resize(cv2.imread(negative_image_paths[i]), None, fx=resize_to[0]/MIN_OBJECT_SCALE, fy=resize_to[1]/MIN_OBJECT_SCALE)

        for x_offset in np.random.permutation(num_detection_windows_along_axis(img.shape[1], stage_idx)):
            if neg_idx >= num_negatives or num_negatives_retrieved_from_img >= num_negatives_per_img: break

            for y_offset in np.random.permutation(num_detection_windows_along_axis(img.shape[0], stage_idx)):
                if neg_idx >= num_negatives or num_negatives_retrieved_from_img >= num_negatives_per_img: break
                x, y, w, h = squash_coords(img, resize_to[0]*x_offset, resize_to[1]*y_offset, *resize_to)

                if (w == resize_to[0] and h == resize_to[1]):
                    images[neg_idx] = img[y:y+h,x:x+w]
                    neg_idx += 1
                    num_negatives_retrieved_from_img += 1

        num_negatives_retrieved_from_img = 0

    if neg_idx < len(images)-1:
        images = np.delete(images, np.s_[neg_idx:], 0)

    with h5py.File(file_name, 'w') as out:
        out.create_dataset(DATASET_LABEL, data=images, chunks=(CHUNK_SIZE,) + (images.shape[1:]))

def mine_negatives(stage_idx, neg_img_folder=NEGATIVE_IMAGE_FOLDER, num_negatives=TARGET_NUM_NEGATIVES):
    """
    Creates a negative dataset file by mining samples from a set of non-object images.

    Parameters
    ----------
    stage_idx: int
        Cascade stage index.
    neg_img_folder: str, optional
        Path to the folder which contains the non-object images, defaults to `NEGATIVE_IMAGE_FOLDER`.
    num_negatives: int, optional
        Maximum number of negative samples to retrieve, defaults to `TARGET_NUM_NEGATIVES`.
    
    Notes
    ----------
    If this function succeeds, a file will be created in the current working directory with the name `NEGATIVE_DATABASE_PATHS[stage_idx]`.

    This function mines negative samples by gathering false positives from the previous stage's classifier and calibrator pair.
    
    Returns
    -------
    None
    """

    from .detect import detect_multiscale

    file_name = NEGATIVE_DATABASE_PATHS[stage_idx]
    resize_to = SCALES[stage_idx]
    negative_image_paths = [os.path.join(neg_img_folder, file_name) for file_name in os.listdir(neg_img_folder)]
    images = np.zeros((num_negatives, resize_to[1], resize_to[0], 3), dtype=np.uint8)
    neg_idx = 0

    for i in np.random.permutation(len(negative_image_paths)):
        if neg_idx >= num_negatives: break
        img = cv2.imread(negative_image_paths[i])
        coords = detect_multiscale(img, stage_idx-1)

        for x_min, y_min, x_max, y_max in coords:
            if neg_idx >= num_negatives: break
            x_min, y_min, w, h = squash_coords(img, x_min, y_min, x_max-x_min, y_max-y_min)
            images[neg_idx] = cv2.resize(img[y_min:y_min+h, x_min:x_min+w], resize_to)
            neg_idx += 1

    if neg_idx < len(images)-1:
        images = np.delete(images, np.s_[neg_idx:], 0)

    with h5py.File(file_name, 'w') as out:
        out.create_dataset(DATASET_LABEL, data=images, chunks=(CHUNK_SIZE,) + (images.shape[1:]))

def create_calibration_dataset(stage_idx, get_object_annotations=get_face_annotations, pos_img_folder=POSITIVE_IMAGE_FOLDER, 
                               num_calibration_samples=TARGET_NUM_CALIBRATION_SAMPLES, calib_patterns=CALIB_PATTERNS):
    """
    Creates a calibration dataset file by perturbing a sequence of object annotations.

    Parameters
    ----------
    stage_idx: int
        Cascade stage index.
    get_object_annotations: callable, optional
        Callable that returns a sequence of image annotations with entries in the form
        (<image path>, <x>, <y>, <width>, <height>), defaults to `data.get_face_annotations`. 
    pos_img_folder: str, optional
        Path to the folder which contains the images referenced by the image annotation sequence, defaults to `POSITIVE_IMAGE_FOLDER`.
    num_calibration_samples: int, optional
        Maximum number of calibration samples to retrieve, defaults to `TARGET_NUM_CALIBRATION_SAMPLES`.
    calib_patterns: numpy.ndarray, optional
        Array of possible calibration transformations.
    
    Notes
    ----------
    If this function succeeds, a file will be created in the current working directory with an appropriate name from `CALIBRATION_DATABASE_PATHS`.
    
    Returns
    -------
    None
    """
    
    num_calibration_samples = math.inf if num_calibration_samples is None else num_calibration_samples
    object_annotations = get_object_annotations(pos_img_folder=pos_img_folder)

    resize_to = SCALES[stage_idx]
    dataset_len = len(object_annotations)*len(calib_patterns) if num_calibration_samples == math.inf else num_calibration_samples
    dataset = np.zeros((dataset_len, resize_to[1], resize_to[0], 3), np.uint8)
    labels = np.zeros((dataset_len, 1))
    sample_idx = 0

    file_name = CALIBRATION_DATABASE_PATHS.get(resize_to[0])

    cur_img = None
    prev_img_path = None
    
    for i, (img_path, x, y, w, h) in enumerate(object_annotations):
        if sample_idx >= num_calibration_samples: break

        if img_path != prev_img_path:
            cur_img = cv2.imread(img_path)

        for n, (sn, xn, yn) in enumerate(CALIB_PATTERNS):
            if sample_idx >= num_calibration_samples: break
            box = squash_coords(cur_img, x + int(xn*w), y + int(yn*h), int(w*sn), int(h*sn))

            if box[2] > 0 and box[3] > 0:
                (box_x, box_y, box_w, box_h) = box
                dataset[sample_idx] = cv2.resize(cur_img[box_y:box_y+box_h,box_x:box_x+box_w], resize_to)
                labels[sample_idx] = n 
                sample_idx += 1

    if sample_idx < dataset_len:
        labels = np.delete(labels, np.s_[sample_idx:], 0)
        dataset = np.delete(dataset, np.s_[sample_idx:], 0)

    with h5py.File(file_name, 'w') as out:
        out.create_dataset(LABELS_LABEL, data=labels, chunks=(CHUNK_SIZE, 1))
        out.create_dataset(DATASET_LABEL, data=dataset, chunks=(CHUNK_SIZE, resize_to[1], resize_to[0], 3))

def get_test_image_paths(test_img_db_folder=TEST_IMAGE_DATABASE_FOLDER, test_imgs_folder=TEST_IMAGES_FOLDER):
    """
    Gets a list of test image paths

    Parameters
    ----------
    test_img_db_folder: str, optional
        Path to folder whose files contain the locations of the test images, defaults to `TEST_IMAGE_DATABASE_FOLDER`.
    test_imgs_folder: str, optional
        Path to the folder that contains the test images, defaults to `TEST_IMAGES_FOLDER`.
    
    Returns
    -------
    out: list
    Returns a list of test image paths
    """

    img_paths = []

    for file_name in os.listdir(test_img_db_folder):
        if 'ellipse' not in file_name:
            with open(os.path.join(test_img_db_folder, file_name)) as inFile:
                img_paths.extend(inFile.read().splitlines())

    return [os.path.join(test_imgs_folder, img_path) + '.jpg' for img_path in img_paths]

class DatasetManager():
    """
    Manages dataset files and data normalization for a model.

    Parameters
    ----------
    model: subclass of `ObjectClassifier` or `ObjectCalibrator`
        Model to manage data for
    get_object_annotations: callable, optional
        Callable that returns a sequence of image annotations with entries in the form
        (<image path>, <x>, <y>, <width>, <height>), defaults to `data.get_face_annotations`.
    pos_img_folder: str, optional
        Path to the folder which contains the images referenced by the image annotation sequence, defaults to `POSITIVE_IMAGE_FOLDER`.
    neg_img_folder: str, optional
        Path to the folder which contains the non-object images, defaults to `NEGATIVE_IMAGE_FOLDER`.
    """

    def __init__(self, model, get_object_annotations=get_face_annotations, pos_img_folder=POSITIVE_IMAGE_FOLDER, neg_img_folder=NEGATIVE_IMAGE_FOLDER):
        """
        Retrieves the relevant dataset paths for `model` and fills in any internal attributes.
        """

        from .model import ObjectCalibrator
        self.model = model
        self.is_calib = isinstance(self.model, ObjectCalibrator)
        self.stage_idx = model.get_stage_idx()
        self.pos_dataset_file_path = OBJECT_DATABASE_PATHS[self.stage_idx]
        self.neg_dataset_file_path = NEGATIVE_DATABASE_PATHS[self.stage_idx]
        self.calib_dataset_file_path = CALIBRATION_DATABASE_PATHS[SCALES[self.stage_idx][0]]
        self.pos_img_folder = pos_img_folder
        self.neg_img_folder = neg_img_folder
        self.update_normalizer = False

        self.g_storage = GlobalStorage()
        self.get_object_annotations = get_object_annotations

    def get_params(self):
        """
        Gets the keyword parameters that this DatasetManager instance was constructed with.

        Parameters
        ----------
        None

        Returns
        -------
        out: dict
        Returns the keyword parameters for `DatasetManager.__init__` as a dictionary of name-value pairs.
        """

        return {'get_object_annotations': self.get_object_annotations, 'pos_img_folder': self.pos_img_folder, 'neg_img_folder': self.neg_img_folder}

    def _create_file(self, file_name, **kwargs):
        """
        Creates a model dataset file with name `file_name` in the current working directory.

        Parameters
        ----------
        file_name: str
            Name of the model dataset file that will be created
        **kwargs: dict, optional
            Parameters to pass to the underlying file creation method, defaults to {}.

        Notes
        ----------
        If this function succeeds, a file will be created in the current working directory with the name `file_name`

        Returns
        -------
        None
        """

        if not os.path.isfile(file_name):
            self.update_normalizer = True

            if file_name in OBJECT_DATABASE_PATHS:
                create_positive_dataset(self.stage_idx, **kwargs)
            elif file_name in  NEGATIVE_DATABASE_PATHS:
                (create_negative_dataset if self.stage_idx == 0 else mine_negatives)(self.stage_idx, **kwargs)
            elif file_name in CALIBRATION_DATABASE_PATHS.values():
                create_calibration_dataset(self.stage_idx, **kwargs)

    def get_pos_dataset_file_path(self):
        """
        Creates the positive dataset file (if necessary) for the specified model and then gets its path

        Parameters
        ----------
        None

        Returns
        -------
        out: str
        Returns the path for the specified model's positive dataset file
        """

        self._create_file(self.pos_dataset_file_path, pos_img_folder=self.pos_img_folder, get_object_annotations=self.get_object_annotations)
        return self.pos_dataset_file_path

    def get_neg_dataset_file_path(self):
        """
        Creates the negative dataset file (if necessary) for the specified model and then gets its path

        Parameters
        ----------
        None

        Returns
        -------
        out: str
        Returns the path for the specified model's negative dataset file
        """

        self._create_file(self.neg_dataset_file_path, neg_img_folder=self.neg_img_folder)
        return self.neg_dataset_file_path

    def get_calib_dataset_file_path(self):
        """
        Creates the calibration dataset file (if necessary) for the specified model and then gets its path

        Parameters
        ----------
        None

        Returns
        -------
        out: str
        Returns the path for the specified model's calibration dataset file
        """

        if self.is_calib: self._create_file(self.calib_dataset_file_path, pos_img_folder=self.pos_img_folder, get_object_annotations=self.get_object_annotations)
        return self.calib_dataset_file_path

    def get_labels(self):
        """
        Get the class labels for the specified model's dataset when available

        Parameters
        ----------
        None

        Returns
        -------
        out: numpy.ndarray
        Returns class labels for the specified model's dataset, or None if class labels are not available for the model specified.
        """

        labels = None

        if self.is_calib:
            calib_dataset_file_path = self.get_calib_dataset_file_path()

            with h5py.File(calib_dataset_file_path, 'r') as calib_dataset_file:
                labels = calib_dataset_file[LABELS_LABEL][:]

        return labels

    def get_normalizer(self):
        """
        Get the input data normalizer for the specified model.

        Parameters
        ----------
        None

        Returns
        -------
        out: preprocess.ImageNormalizer
        Returns the input data normalizer for the specified model.
        """

        from .preprocess import ImageNormalizer
        model_name = type(self.model).__name__

        if not self.g_storage.has(model_name) or self.update_normalizer:
            normalizer = ImageNormalizer(self.get_pos_dataset_file_path(), self.get_neg_dataset_file_path(), self.model.get_normalization_method())
            normalizer.add_data_augmentation_params(self.model.get_normalization_params())
            self.g_storage.store(model_name, normalizer)

        return self.g_storage.get(model_name)

    def get_paths(self):
        """
        Get a list of the relevant dataset file paths for the specified model.

        Parameters
        ----------
        None

        Returns
        -------
        out: list
        Returns a list of relevant dataset file paths for the specfied model.
        """

        return [self.get_pos_dataset_file_path(), self.get_neg_dataset_file_path()] if not self.is_calib else [self.get_calib_dataset_file_path(), None]