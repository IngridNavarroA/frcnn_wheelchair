from __future__ import division
import os
import cv2
import numpy as np
import sys
import pickle
from optparse import OptionParser
import time
from keras_frcnn import config
from keras import backend as K
from keras.layers import Input
from keras.models import Model
from keras_frcnn import roi_helpers
# np.set_printoptions(threshold='nan')
sys.setrecursionlimit(40000)

"""----------------------------------------------------------------------------
                                PARSING OPTIONS 
----------------------------------------------------------------------------"""
parser = OptionParser()

parser.add_option("-p", 
                  "--path", 
                  dest="test_path", 
                  help="Path to test data.")

parser.add_option("-n", 
                  "--num_rois", 
                  dest="num_rois",
			  help="Number of ROIs per iteration. Higher means more memory use.", 
                  default=32)

parser.add_option("--config_filename", 
                  dest="config_filename", 
                  help="Location to read the metadata related to the training (generated when training).",
			  default="config.pickle")

parser.add_option("--network", 
                  dest="network", 
                  help="Base network to use. Supports vgg or resnet50.", 
                  default='resnet50')

(options, args) = parser.parse_args()

options.test_path = 'images/videos/'
options.network = 'resnet50'
if not options.test_path:   # if filename is not given
	parser.error('Error: path to test data must be specified. Pass --path to command line')

"""----------------------------------------------------------------------------
                                CONFIGURATION
----------------------------------------------------------------------------"""
config_output_filename = options.config_filename


with open(config_output_filename, 'rb') as f_in: C = pickle.load(f_in)

if C.network == 'resnet50': import keras_frcnn.resnet as nn
elif C.network == 'vgg': import keras_frcnn.vgg as nn

# turn off any data augmentation at test time
C.use_horizontal_flips = False
C.use_vertical_flips = False
C.rot_90 = False

img_path = options.test_path

# formats the image size based on config 
def format_img_size(img, C):	
	img_min_side = float(C.im_size)
	(height,width,_) = img.shape
		
	if width <= height:
		ratio = img_min_side / width
		new_height = int(ratio * height)
		new_width = int(img_min_side)
	else:
		ratio = img_min_side / height
		new_width = int(ratio * width)
		new_height = int(img_min_side)
  
	img = cv2.resize(img, (new_width, new_height), interpolation=cv2.INTER_CUBIC)
	return img, ratio	
 
 # formats the image channels based on config
def format_img_channels(img, C):
	img = img[:, :, (2, 1, 0)]
	img = img.astype(np.float32)
	img[:, :, 0] -= C.img_channel_mean[0]
	img[:, :, 1] -= C.img_channel_mean[1]
	img[:, :, 2] -= C.img_channel_mean[2]
	img /= C.img_scaling_factor
	img = np.transpose(img, (2, 0, 1))
	img = np.expand_dims(img, axis=0)
	return img

# formats an image for model prediction based on config	
def format_img(img, C):	
     img, ratio = format_img_size(img, C)
     img = format_img_channels(img, C)
     return img, ratio

# Method to transform the coordinates of the bounding box to its original size
def get_real_coordinates(ratio, x1, y1, x2, y2):
	real_x1 = int(round(x1 // ratio))
	real_y1 = int(round(y1 // ratio))
	real_x2 = int(round(x2 // ratio))
	real_y2 = int(round(y2 // ratio))
	return (real_x1, real_y1, real_x2, real_y2)

class_mapping = C.class_mapping

if 'bg' not in class_mapping:
	class_mapping['bg'] = len(class_mapping)

class_mapping = {v: k for k, v in class_mapping.items()}
print(class_mapping)

class_to_color = { class_mapping[v]: 
                    np.random.randint(0, 255, 3) for v in class_mapping }

C.num_rois = int(options.num_rois)

if C.network == 'resnet50':
	num_features = 1024
elif C.network == 'vgg':
	num_features = 512

if K.image_dim_ordering() == 'th':
	input_shape_img = (3, None, None)
	input_shape_features = (num_features, None, None)
else:
	input_shape_img = (None, None, 3)
	input_shape_features = (None, None, num_features)


"""----------------------------------------------------------------------------
                            DEFINE NETWORK 
----------------------------------------------------------------------------"""
img_input = Input(shape=input_shape_img)
roi_input = Input(shape=(C.num_rois, 4))
feature_map_input = Input(shape=input_shape_features)

# define the base network (resnet here, can be VGG, Inception, etc)
shared_layers = nn.nn_base(img_input, trainable=True)
num_anchors = len(C.anchor_box_scales) * len(C.anchor_box_ratios)

# define the RPN, built on the base layers
rpn_layers = nn.rpn(shared_layers, num_anchors)

# define the classifier 
classifier = nn.classifier(feature_map_input, roi_input, C.num_rois, 
                           nb_classes=len(class_mapping), trainable=True)

# define network models
model_rpn = Model(img_input, rpn_layers)
model_classifier_only = Model([feature_map_input, roi_input], classifier)
model_classifier = Model([feature_map_input, roi_input], classifier)

# load model weights
print('Loading weights from {}'.format(C.model_path))
model_rpn.load_weights(C.model_path, by_name=True)
model_classifier.load_weights(C.model_path, by_name=True)

# compile models 
model_rpn.compile(optimizer='sgd', loss='mse')
model_classifier.compile(optimizer='sgd', loss='mse')

all_imgs = []
classes = {}
bbox_threshold = 0.84
visualise = True

"""----------------------------------------------------------------------------
                            TEST VIDEO 
----------------------------------------------------------------------------"""

# video reader
cap = cv2.VideoCapture('/home/inavarro/Desktop/keras/images/wheelchair_videos/wheelchair_video_10.MP4')

# video writer
# fourcc = cv2.cv.CV_FOURCC(*'XVID')
# fourcc = cv2.VideoWriter_fourcc(*'XVID')
# fourcc = cv2.VideoWriter_fourcc('M','P','4','2')
# out_vid = cv2.VideoWriter('wheelchair_detection_test2.MP4', fourcc, 10.0, (600, 480))

x = 0
curr_frame = 0
last_frame = -10
cv2.namedWindow('frame', cv2.WINDOW_NORMAL)
cv2.resizeWindow('frame', 600, 480)

while(cap.isOpened()):
    
    ret, frame = cap.read()
    # gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    # cv2.imshow('frame', frame) 
    curr_frame = cap.get(1) # (cv2.CV_CAP_PROP_POS_FRAMES)
    if curr_frame == (last_frame + 11):
        
        last_frame = curr_frame
        
        X, ratio = format_img(frame, C)    
        st = time.time()
        # transpose image with tensorflow 
        X = np.transpose(X, (0, 2, 3, 1))
        
        # get the feature maps and output from the RPN
        [Y1, Y2, F] = model_rpn.predict(X)
        
        # convert from rpn to roi 
        R = roi_helpers.rpn_to_roi(Y1, Y2, C, K.image_dim_ordering(), overlap_thresh=0.7)
        
        # convert from (x1,y1,x2,y2) to (x,y,w,h)
        R[:, 2] -= R[:, 0]
        R[:, 3] -= R[:, 1]    
        
        # apply the spatial pyramid pooling (SPP) to the proposed regions
        bboxes = {}
        probs = {}
        
        for jk in range(R.shape[0]//C.num_rois + 1):
            ROIs = np.expand_dims(R[C.num_rois*jk:C.num_rois*(jk+1), :], axis=0)
            
            if ROIs.shape[1] == 0:
                break
            
            if jk == R.shape[0]//C.num_rois:
                #pad R
                curr_shape = ROIs.shape
                target_shape = (curr_shape[0], C.num_rois, curr_shape[2])
                ROIs_padded = np.zeros(target_shape).astype(ROIs.dtype)
                ROIs_padded[:, :curr_shape[1], :] = ROIs
                ROIs_padded[0, curr_shape[1]:, :] = ROIs[0, 0, :]
                ROIs = ROIs_padded
                
            # predict class and bbox regresor
            [P_cls, P_regr] = model_classifier_only.predict([F, ROIs])
            
            for ii in range(P_cls.shape[1]):
                
                if np.max(P_cls[0, ii, :]) < bbox_threshold or np.argmax(P_cls[0, ii, :]) == (P_cls.shape[2] - 1):
                    continue
                
                cls_name = class_mapping[np.argmax(P_cls[0, ii, :])]
                
                if cls_name not in bboxes:
                    bboxes[cls_name] = []
                    probs[cls_name] = []
                    
                (x, y, w, h) = ROIs[0, ii, :]
                    
                cls_num = np.argmax(P_cls[0, ii, :])
                    
                try:
                    (tx, ty, tw, th) = P_regr[0, ii, 4*cls_num:4*(cls_num+1)]
                    tx /= C.classifier_regr_std[0]
                    ty /= C.classifier_regr_std[1]
                    tw /= C.classifier_regr_std[2]
                    th /= C.classifier_regr_std[3]
                    x, y, w, h = roi_helpers.apply_regr(x, y, w, h, tx, ty, tw, th)
                except:
                    pass
                    
                bboxes[cls_name].append([C.rpn_stride*x, C.rpn_stride*y, 
                                             C.rpn_stride*(x+w), C.rpn_stride*(y+h)])
                
                probs[cls_name].append(np.max(P_cls[0, ii, :]))
                    
    all_dets = []
    
    for key in bboxes:
        bbox = np.array(bboxes[key])
        
        # reduce redundancy with NMS 
        new_boxes, new_probs = roi_helpers.non_max_suppression_fast(bbox, np.array(probs[key]), overlap_thresh=0.5)
        
        for jk in range(new_boxes.shape[0]):
        
            # get coordinates       
            (x1, y1, x2, y2) = new_boxes[jk,:]
            
            # get real coordinates 
            (real_x1, real_y1, real_x2, real_y2) = get_real_coordinates(ratio, x1, y1, x2, y2)
                        
            # draw rectangle where bbox was predicted
            cv2.rectangle(frame, 
                          (real_x1, real_y1), 
                          (real_x2, real_y2), 
                          (int(class_to_color[key][0]), 
                           int(class_to_color[key][1]), 
                           int(class_to_color[key][2])),2)
                           
            # create text label 
            textLabel = '{}: {}'.format(key,float("{0:.3f}".format(new_probs[jk])))
            all_dets.append((key, float("{0:.4f}".format(new_probs[jk]))))
            (retval, baseLine) = cv2.getTextSize(textLabel, cv2.FONT_HERSHEY_PLAIN,1, 1)
            textOrg = (real_x1, real_y1-0)  
            
            # create text with predicted class 
            cv2.rectangle(frame, 
                          (textOrg[0] - 5, textOrg[1] + baseLine - 5), 
                          (textOrg[0] + retval[0] + 5, textOrg[1] - retval[1] - 5), 
                          (0, 0, 0), 2)
                          
            # create rectangle for text label 
            cv2.rectangle(frame, 
                          (textOrg[0] - 5, textOrg[1]+ baseLine - 5), 
                          (textOrg[0] + retval[0] + 5, textOrg[1] - retval[1] - 5), 
                          (int(class_to_color[key][0]), 
                           int(class_to_color[key][1]), 
                           int(class_to_color[key][2])), -1)
            
            # add text tabel to image
            cv2.putText(frame, textLabel, textOrg, cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 0), 1)
            
    # show image and prediction time 
    print('Elapsed time = {}'.format(time.time() - st))
    print(all_dets)
    
    cv2.imshow('frame', frame)
    # cv2.imwrite('./results/vid1/video_detected_{}.png'.format(x),frame)
    # out_vid.write(frame)
    if cv2.waitKey(1) & 0xFF == ord('q'):
        break

cap.release()  
# out_vid.release()
cv2.destroyAllWindows()