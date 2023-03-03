from __future__ import absolute_import, division, print_function

import os
import sys
import glob
import argparse
import numpy as np
import PIL.Image as pil
import matplotlib as mpl
import matplotlib.cm as cm
import cv2
import torch
from torchvision import transforms, datasets

import networks
from layers import disp_to_depth
from utils import download_model_if_doesnt_exist
from evaluate_depth import STEREO_SCALE_FACTOR
import glob
import natsort
from tqdm import tqdm 
from datetime import datetime

timestamp = datetime.now().strftime("%m_%d_%Y_%H_%M")

def depth_value_to_depth_image(depth_values):
    depth_values = cv2.normalize(depth_values, None, 0, 1, norm_type=cv2.NORM_MINMAX, dtype=cv2.CV_64F)
    depth = (depth_values * 255).astype(np.uint8)
    depth = cv2.applyColorMap(depth, cv2.COLORMAP_MAGMA)
    return depth


def parse_args():
    parser = argparse.ArgumentParser(
        description='Simple testing funtion for Monodepthv2 models.')

    # parser.add_argument('--image_path', type=str,
    #                     help='path to a test image or folder of images', required=True)
    parser.add_argument('--model_name', type=str,
                        help='name of a pretrained model to use',
                        choices=[
                            "mono_640x192",
                            "stereo_640x192",
                            "mono+stereo_640x192",
                            "mono_no_pt_640x192",
                            "stereo_no_pt_640x192",
                            "mono+stereo_no_pt_640x192",
                            "mono_1024x320",
                            "stereo_1024x320",
                            "mono+stereo_1024x320"])
    # parser.add_argument('--ext', type=str,
    #                     help='image extension to search for in folder', default="jpg")
    parser.add_argument("--no_cuda",
                        help='if set, disables CUDA',
                        action='store_true')
    parser.add_argument("--pred_metric_depth",
                        help='if set, predicts metric depth instead of disparity. (This only '
                             'makes sense for stereo-trained KITTI models).',
                        action='store_true')

    return parser.parse_args()


def test_simple(args):
    """Function to predict for a single image or folder of images
    """
    assert args.model_name is not None, \
        "You must specify the --model_name parameter; see README.md for an example"

    if torch.cuda.is_available() and not args.no_cuda:
        device = torch.device("cuda")
    else:
        device = torch.device("cpu")

    if args.pred_metric_depth and "stereo" not in args.model_name:
        print("Warning: The --pred_metric_depth flag only makes sense for stereo-trained KITTI "
              "models. For mono-trained models, output depths will not in metric space.")

    download_model_if_doesnt_exist(args.model_name)
    model_path = os.path.join("models", args.model_name)
    print("-> Loading model from ", model_path)
    encoder_path = os.path.join(model_path, "encoder.pth")
    depth_decoder_path = os.path.join(model_path, "depth.pth")

    # LOADING PRETRAINED MODEL
    print("   Loading pretrained encoder")
    encoder = networks.ResnetEncoder(18, False)
    loaded_dict_enc = torch.load(encoder_path, map_location=device)

    # extract the height and width of image that this model was trained with
    feed_height = loaded_dict_enc['height']
    feed_width = loaded_dict_enc['width']
    filtered_dict_enc = {k: v for k, v in loaded_dict_enc.items() if k in encoder.state_dict()}
    encoder.load_state_dict(filtered_dict_enc)
    encoder.to(device)
    encoder.eval()

    print("   Loading pretrained decoder")
    depth_decoder = networks.DepthDecoder(
        num_ch_enc=encoder.num_ch_enc, scales=range(4))

    loaded_dict = torch.load(depth_decoder_path, map_location=device)
    depth_decoder.load_state_dict(loaded_dict)

    depth_decoder.to(device)
    depth_decoder.eval()
###########################################################################
    save_img_dir = f'./results/submission_{timestamp}'
    if not os.path.isdir(save_img_dir):
        os.makedirs(save_img_dir)

    output_directory = './results'
    file_name = '/home/sadath/NeWCRFs/data_splits/val_files_CVPR.txt'

    f = open(file_name, "r")
    lines = f.readlines()
    test_image_paths = ['/hdd/team_2/syns_patches/'+x.strip() for x in natsort.natsorted(lines)]

    image_names = ['_'.join(x.split('/')[4:]) for x in test_image_paths]
    # print(image_name)

    pred_depths = []
    # PREDICTING ON EACH IMAGE IN TURN
    with torch.no_grad():
        for idx, (image_path, image_name) in enumerate(tqdm(zip(test_image_paths, image_names))):
            print(image_name)

            # if idx ==5:
            #     break
            image_vis = cv2.imread(image_path)

            # Load image and preprocess
            input_image = pil.open(image_path).convert('RGB')
            original_width, original_height = input_image.size
            input_image = input_image.resize((feed_width, feed_height), pil.LANCZOS)
            input_image = transforms.ToTensor()(input_image).unsqueeze(0)

            # PREDICTION
            input_image = input_image.to(device)
            features = encoder(input_image)
            outputs = depth_decoder(features)

            disp = outputs[("disp", 0)]
            disp_resized = torch.nn.functional.interpolate(
                disp, (original_height, original_width), mode="bilinear", align_corners=False)

            # Saving numpy file
            scaled_disp, depth = disp_to_depth(disp_resized, 0.1, 100)

            # Saving colormapped depth image
            disp_resized_np = disp_resized.squeeze().cpu().numpy()
            vmax = np.percentile(disp_resized_np, 95)
            normalizer = mpl.colors.Normalize(vmin=disp_resized_np.min(), vmax=vmax)
            mapper = cm.ScalarMappable(norm=normalizer, cmap='magma')
            colormapped_im = (mapper.to_rgba(disp_resized_np)[:, :, :3] * 255).astype(np.uint8)
            im = pil.fromarray(colormapped_im)
            name_dest_im = os.path.join(save_img_dir,'{}'.format(image_name))

            # disparity_vis = depth_value_to_depth_image(im)
            
            # vis = np.hstack([image_vis, im])
            # cv2.imwrite(name_dest_im, image_vis)
                
            name_dest_im = os.path.join(save_img_dir,'{}'.format(image_name))
            im.save(name_dest_im)

            pred_depths.append(scaled_disp.cpu().numpy().squeeze())
    
    print(len(pred_depths))
    np.savez_compressed(save_img_dir+'/pred.npz', pred=pred_depths)
    output_path_disp= os.path.join(save_img_dir, "dis_monodepth2")
    np.save((output_path_disp), pred_depths)
    
    print('-> Done!')

    print('changed@!!!')
if __name__ == '__main__':
    args = parse_args()
    test_simple(args)