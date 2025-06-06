import torch
import torch.nn as nn
import torch.nn.functional as F
from functools import partial
from torchsummary import summary
from timm.models.layers import DropPath, trunc_normal_
import os
import cv2
import numpy
import numpy as np
import time
from torch.utils.tensorboard import SummaryWriter
from torchvision.utils import make_grid
from MobileNetV2 import mobilenet_v2
writer = SummaryWriter('log/run' + time.strftime("%d-%m"))

class JLModule(nn.Module):
    def __init__(self, backbone):
        super(JLModule, self).__init__()
        self.backbone = backbone
        

    def load_pretrained_model(self, model_path):
        pretrained_dict = torch.load(model_path)
        model_dict = self.backbone.state_dict()
        pretrained_dict = {ka: va for ka, va in pretrained_dict.items() if ka in model_dict}
        model_dict.update(pretrained_dict)
        self.backbone.load_state_dict(model_dict)
        

    def forward(self, x):

        convr = self.backbone(x)
        print("Backbone Features shape")
        print("RGB1: ",convr[1].shape)
        print("RGB2: ",convr[2].shape)
        print("RGB3: ",convr[3].shape)
        print("RGB4: ",convr[4].shape)
     
        

        return convr[1], convr[2], convr[3], convr[4]

class ShuffleChannelAttention(nn.Module):
    def __init__(self, channel=64,reduction=16,kernel_size=3,groups=8):
        super(ShuffleChannelAttention, self).__init__()
        self.maxpool=nn.AdaptiveMaxPool2d(1)
        self.g=groups
        self.se=nn.Sequential(
            nn.Conv2d(channel,channel//reduction,1,padding=1,bias=False),
            nn.ReLU(),
            nn.Conv2d(channel//reduction,channel,3,bias=False)
        )
        self.sigmoid=nn.Sigmoid()
        
    
    def forward(self, x) :
        b,c,h,w=x.shape
        residual=x
        max_result=self.maxpool(x)
        #print('***Shuffle chaneel***')
        #print('max',max_result.shape)
        shuffled_in=max_result.view(b,self.g,c//self.g,1,1).permute(0,2,1,3,4).reshape(b,c,1,1)
        #print('shuffled',shuffled_in.shape)
        max_out=self.se(shuffled_in)
        #print('se',max_out.shape)
        output1=self.sigmoid(max_out)
        output1=output1.view(b,c,1,1)
        #print('output1',output1.shape)
        output2=self.sigmoid(max_result)
        output=output1+output2
        return (output*x)+residual

class LDELayer(nn.Module):
    def __init__(self):
        super(LDELayer, self).__init__()
        self.operation_stage_1=nn.Sequential(nn.Conv2d(32,32,kernel_size=3,stride=1,padding=1,dilation=1), nn.ReLU())  

        self.ca_1=ShuffleChannelAttention(channel=32,reduction=16,kernel_size=3,groups=2)

        #self.upsample=nn.ConvTranspose2d(576, 64, kernel_size=3, stride=4, padding=1, output_padding=3,dilation=1)
        #self.upsample_1=nn.ConvTranspose2d(384, 96, kernel_size=3, stride=4, padding=1, output_padding=3,dilation=1)
        #self.conv1x1=nn.Conv2d(576,384,1,1)
        self.last_conv1x1=nn.Conv2d(32,1,1,1)
       

    def forward(self, list_x):
        lde_out=[]
        
        rgb_conv = list_x
        depth_tran = list_x
        #print("******LDE layer******")
        #print(rgb_conv.shape,depth_tran.shape)
        rgb_1=self.operation_stage_1(list_x)
        depth_1=self.ca_1(list_x)
        rgbd_fusion_1=list_x+(rgb_1*depth_1)
        #print('rgbd_fusion_1',rgbd_fusion_1.shape)  
        last_out=self.last_conv1x1(rgbd_fusion_1)
        #print('last',last_out.shape)
        
        return last_out


class CoarseLayer(nn.Module):
    def __init__(self):
        super(CoarseLayer, self).__init__()
        self.relu = nn.ReLU()
        self.conv_r = nn.Sequential(nn.Conv2d(320,160,1,1),self.relu,nn.Conv2d(160, 1, 1, 1))
        
        

    def forward(self, x):

        sal_rgb=self.conv_r(x)
        
        #print('sal r  ',sal_rgb.shape)
        return sal_rgb

class GDELayer(nn.Module):
    def __init__(self):
        super(GDELayer, self).__init__()
        k=1
        self.sigmoid = nn.Sigmoid()
        self.relu = nn.ReLU()
        self.convH=nn.Sequential(nn.Conv2d(320,160,1,1),self.relu,nn.Conv2d(160,1,1,1))
        self.convM=nn.Sequential(nn.Conv2d(96,32,1,1),self.relu,nn.Conv2d(32,1,1,1))
        self.convL=nn.Sequential(nn.Conv2d(32,24,1,1),self.relu,nn.Conv2d(24,1,1,1))
        
        #self.conv384=nn.Sequential(nn.Conv2d(576,192,1,1),self.relu,nn.Conv2d(192,1,1,1))
        self.upsampling= nn.ConvTranspose2d(k,k, kernel_size=4, stride=2 , padding=1) # 10x10 to 20x20
        #self.upsampling11= nn.ConvTranspose2d(k,k, kernel_size=4, stride=4 , padding=0)# 10x10 to 40x40
        #self.upsampling12=nn.ConvTranspose2d(1,1, kernel_size=5, stride=8 , padding=0,output_padding=3) # 10x10 t0 80x80
        #self.upsampling22= nn.ConvTranspose2d(576,k, kernel_size=4, stride=2 , padding=1) 
        #self.upsampling222= nn.ConvTranspose2d(576,1, kernel_size=4, stride=4 , padding=0)
        

    def forward(self, convr4, convr5, coarse_sal_rgb):
        #print('********GDE layer*******')
        #rgb_h=torch.zeros(coarse_sal_rgb.size(0),1,10,10).cuda()
        #rgb_m=torch.zeros(coarse_sal_rgb.size(0),1,20,20).cuda()
        convr5_rgb_part=self.convH(convr5)
        salr=self.sigmoid(coarse_sal_rgb)
        Ar=1-salr
        rgb_h=Ar*convr5_rgb_part
        #print('reverse',rgb_h.shape)
        convr4_rgb_part=self.convM(convr4)
        coarse_sal_rgb1=self.upsampling(coarse_sal_rgb)
        salr=self.sigmoid(coarse_sal_rgb1)
        Ar=1-salr
        rgb_m=Ar*convr4_rgb_part
        #print('reverse',rgb_m.shape)
       
        '''for j in range(11,7,-3):
            rgb_part=x[j]
            depth_part=y[j]
            B, _, C = depth_part.shape
            Br,Cr,Hr,Wr=x[j].shape
            # [N, 197, 384] -> [N, 196, 384] -> [N, 384, 196] -> [N, 384, 14, 14]

            #x_r = self.act(self.bn(self.conv_project(x_r)))
      
            #print('before j rgb depth',j,rgb_part.shape,depth_part.shape)
            if (rgb_part.size(1)==1536):
                
                coarse_sal_rgb1=self.upsampling(coarse_sal_rgb)
                coarse_sal_depth1=self.upsampling(coarse_sal_depth)
                y_r = depth_part[:, 1:].transpose(1, 2).unflatten(2,(20,20))
                y_r=self.conv384(y_r)

                salr=self.sigmoid(coarse_sal_rgb1)
                Ar=1-salr
                rgb_h+=Ar*rgb_part

                sald=self.sigmoid(coarse_sal_depth1)
                Ad=1-sald
                depth_h+=Ad*y_r
                #print('j, rgb after,coarse_rgb_after,depth after, coarse_depth_after,Ar,Ad',j,rgb_part.shape,coarse_sal_rgb1.shape,y_r.shape,coarse_sal_depth1.shape,Ar.shape,Ad.shape)
            


            else:
                rgb_part=self.conv512(rgb_part)
                coarse_sal_rgb1=self.upsampling11(coarse_sal_rgb)
                coarse_sal_depth1=self.upsampling11(coarse_sal_depth)
                y_r = depth_part[:, 1:].transpose(1, 2).unflatten(2,(20,20))
                y_r=self.upsampling22(y_r)

                salr=self.sigmoid(coarse_sal_rgb1)
                Ar=1-salr
                rgb_m+=Ar*rgb_part

                sald=self.sigmoid(coarse_sal_depth1)
                Ad=1-sald
                depth_m+=Ad*y_r
                #print('j, rgb after,coarse_rgb_after,depth after, coarse_depth_after,Ar,Ad',j,rgb_part.shape,coarse_sal_rgb1.shape,y_r.shape,coarse_sal_depth1.shape,Ar.shape,Ad.shape)
                
        j=4
        rgb_part=x[j]
        depth_part=y[j]
        B, _, C = depth_part.shape
        Br,Cr,Hr,Wr=x[j].shape
        # [N, 197, 384] -> [N, 196, 384] -> [N, 384, 196] -> [N, 384, 14, 14]

        rgb_part=self.conv256(rgb_part)
        coarse_sal_rgb1=self.upsampling12(coarse_sal_rgb)
        coarse_sal_depth1=self.upsampling12(coarse_sal_depth)
        y_r = depth_part[:, 1:].transpose(1, 2).unflatten(2,(20,20))
        y_r=self.upsampling222(y_r)

        salr=self.sigmoid(coarse_sal_rgb1)
        Ar=1-salr
        rgb_l+=Ar*rgb_part

        sald=self.sigmoid(coarse_sal_depth1)
        Ad=1-sald
        depth_l+=Ad*y_r'''
        #print('j, rgb after,coarse_rgb_after,depth after, coarse_depth_after,Ar,Ad',j,rgb_part.shape,coarse_sal_rgb1.shape,y_r.shape,coarse_sal_depth1.shape,Ar.shape,Ad.shape)
            
            
        #print('gde',rgb_h.shape,rgb_m.shape,depth_h.shape,depth_m.shape)     
        return rgb_h,rgb_m

class Decoder(nn.Module):
    def __init__(self):
        super(Decoder, self).__init__()
        self.upsample=nn.ConvTranspose2d(1, 1, kernel_size=3, stride=2, padding=1, output_padding=1,dilation=1)
        #self.upsample1=nn.ConvTranspose2d(576, 1, kernel_size=3, stride=4, padding=1, output_padding=3,dilation=1)
        self.up2= nn.ConvTranspose2d(1, 1, kernel_size=4, stride=2, padding=1) 
        #self.up2= nn.ConvTranspose2d(1, 1, kernel_size=3, stride=2, padding=2)
        self.up21= nn.ConvTranspose2d(1, 1, kernel_size=4, stride=2, padding=1) 
        self.act=nn.Sigmoid()
        
        
        
    def forward(self, lde_out ,rgb_h,rgb_m):
      
        lde_out1=self.upsample(lde_out)
      

        #lde_out2=self.upsample(lde_out[1])
        

        #lde_out3=self.upsample(lde_out[2])
        
        edge_rgbd0=self.act(self.up21(self.up21(lde_out1)))
        
        #edge_rgbd1=self.act(self.up21(lde_out2))
        #edge_rgbd2=self.act(self.up21(lde_out3))
        #print(self.up2(sal_high).shape,self.up2(sal_med).shape,self.up2(sal_low).shape,  edge_rgbd0.shape,  edge_rgbd1.shape,  edge_rgbd2.shape)
        sal_final=edge_rgbd0+self.up21(self.up2(self.up2(self.up2((rgb_m+(self.up2(rgb_h)))))))
        #print(edge_rgbd0.shape, sal_final.shape)

        return sal_final,edge_rgbd0


class JL_DCF(nn.Module):
    def __init__(self,JLModule,lde_layers,coarse_layer,gde_layers,decoder):
        super(JL_DCF, self).__init__()
        
        self.JLModule = JLModule
        self.lde = lde_layers
        self.coarse_layer=coarse_layer
        self.gde_layers=gde_layers
        self.decoder=decoder
        self.final_conv=nn.Conv2d(8,1,1,1,0)
        
    def forward(self, f_all):
        conv1r, conv2r, conv3r, conv4r = self.JLModule(f_all)
        lde_out = self.lde(conv2r)
        coarse_sal_rgb=self.coarse_layer(conv4r)
        rgb_h,rgb_m=self.gde_layers(conv3r, conv4r, coarse_sal_rgb)

        sal_final,edge_rgbd0=self.decoder(lde_out ,rgb_h,rgb_m)

        return sal_final,coarse_sal_rgb,edge_rgbd0
        #,lde_out,rgb_h,rgb_m

def build_model(network='conformer', base_model_cfg='conformer'):
   
        backbone= mobilenet_v2()
        
   

        return JL_DCF(JLModule(backbone),LDELayer(),CoarseLayer(),GDELayer(),Decoder())
