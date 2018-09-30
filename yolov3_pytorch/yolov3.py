
from collections import OrderedDict, Iterable, defaultdict
import torch
import torch.nn as nn
import torch.nn.functional as F

import importlib
from .yolo_layer import *
from .yolov3_base import *

class Darknet(nn.Module):
    def __init__(self, num_blocks, start_nf=32):
        super().__init__()
        nf = start_nf
        self.base = ConvBN(3, nf, kernel_size=3, stride=1) #, padding=1)
        self.layers = []
        for i, nb in enumerate(num_blocks):
            # dn_layer = make_group_layer(nf, nb, stride=(1 if i==-1 else 2))
            dn_layer = self.make_group_layer(nf, nb, stride=2)
            self.add_module(f"darknet_{i}", dn_layer)
            self.layers.append(dn_layer)
            nf *= 2

    def make_group_layer(self, ch_in, num_blocks, stride=2):
        layers = [ConvBN(ch_in, ch_in*2, stride=stride)]
        for i in range(num_blocks): layers.append(DarknetBlock(ch_in*2))
        return nn.Sequential(*layers)

    def forward(self, x):
        y = [self.base(x)]
        for l in self.layers:
            y.append(l(y[-1]))
        return y

class Yolov3UpsamplePrep(nn.Module):
    def __init__(self, filters_list, in_filters, out_filters):
        super().__init__()
        self.branch = nn.ModuleList([
                        ConvBN(in_filters, filters_list[0], 1),
                        ConvBN(filters_list[0], filters_list[1], kernel_size=3),
                        ConvBN(filters_list[1], filters_list[0], kernel_size=1),
                        ConvBN(filters_list[0], filters_list[1], kernel_size=3),
                        ConvBN(filters_list[1], filters_list[0], kernel_size=1),])
        self.for_yolo = nn.ModuleList([
                        ConvBN(filters_list[0], filters_list[1], kernel_size=3),
                        nn.Conv2d(filters_list[1], out_filters, kernel_size=1, stride=1,
                                   padding=0, bias=True)])
#         self.upsample = upsample
        
    def forward(self, x):
        for m in self.branch: x = m(x)
        branch_out = x
        for m in self.for_yolo: x = m(x)
#         if self.upsample:
#             x = nn.Upsample(scale_factor=2, mode='nearest')(x)
        #return namedtuple('out', ['branch', 'for_yolo'])(branch_out, x)
        return branch_out, x

class Yolov3(nn.Module):
    def __init__(self, num_classes=80):
        super().__init__()
        self.backbone = Darknet([1,2,8,8,4])
        
        anchors_per_region = 3
        self.yolo_0_pre = Yolov3UpsamplePrep([512, 1024], 1024, anchors_per_region*(5+num_classes))
        self.yolo_0 = YoloLayer(anchors=[(116.,  90.), (156., 198.), (373., 326.)], stride=32, num_classes=num_classes)

        self.yolo_1_c = ConvBN(512, 256, 1)
        self.yolo_1_prep = Yolov3UpsamplePrep([256, 512], 512+256, anchors_per_region*(5+num_classes))
        self.yolo_1 = YoloLayer(anchors=[(30., 61.), (62., 45.), (59., 119.)], stride=16, num_classes=num_classes)

        self.yolo_2_c = ConvBN(256, 128, 1)
        self.yolo_2_prep = Yolov3UpsamplePrep([128, 256], 256+128, anchors_per_region*(5+num_classes))
        self.yolo_2 = YoloLayer(anchors=[(10., 13.), (16., 30.), (33., 23.)], stride=8, num_classes=num_classes)
        

    def get_loss_layers(self):
        return [self.yolo_0, self.yolo_1, self.yolo_2]
        

    def forward(self, x, debug=False):
        xb = self.backbone(x)
        # if debug: print_tensor_shapes(xb)
        
        x, y0 = self.yolo_0_pre(xb[-1])
        # if debug: print_tensor_shapes([x, y0])
        
        # if debug: print("now y1")
        x = self.yolo_1_c(x)
        # if debug: print_tensor_shapes([x])
        x = nn.Upsample(scale_factor=2, mode='nearest')(x)
        # if debug: print_tensor_shapes([x])
        x = torch.cat([x, xb[-2]], 1)
        # if debug: print_tensor_shapes([x])
        x, y1 = self.yolo_1_prep(x)
        # if debug: print_tensor_shapes([x, y1])

        # if debug: print("now y2")
        x = self.yolo_2_c(x)
        # if debug: print_tensor_shapes([x])
        x = nn.Upsample(scale_factor=2, mode='nearest')(x)
        # if debug: print_tensor_shapes([x])
        x = torch.cat([x, xb[-3]], 1)
        # if debug: print_tensor_shapes([x])
        x, y2 = self.yolo_2_prep(x)
        # if debug: print_tensor_shapes([x, y2])
        
        return y0, y1, y2



    def boxes_from_output(self, outputs, conf_thresh=0.25):
        all_boxes = [[] for j in range(outputs[0].size(0))]
        for i, layer in enumerate(self.get_loss_layers()):
            layer_boxes = layer.get_region_boxes(outputs[i], conf_thresh=conf_thresh)
            for j, layer_box in enumerate(layer_boxes):
                all_boxes[j] += layer_box

        return all_boxes


    def predict_img(self, imgs, conf_thresh=0.25):
        self.eval()
        if len(imgs.shape) == 3: imgs = imgs.unsqueeze(-1) 
        
        outputs = self.forward(imgs)
        return self.boxes_from_output(outputs, conf_thresh)




