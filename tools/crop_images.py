#!/usr/bin/env python
# -*- coding: utf-8 -*-

###############################################################################
#  Copyright 2017 by Kitware Inc.
#
#  Licensed under the Apache License, Version 2.0 ( the "License" );
#  you may not use this file except in compliance with the License.
#  You may obtain a copy of the License at
#
#    http://www.apache.org/licenses/LICENSE-2.0
#
#  Unless required by applicable law or agreed to in writing, software
#  distributed under the License is distributed on an "AS IS" BASIS,
#  WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#  See the License for the specific language governing permissions and
#  limitations under the License.
###############################################################################

import gdalconst

# from gaia.geo.gdal_functions import *
# from gaia.geo.processes_raster import *

import os
import fnmatch

import traceback
import sys
import json

import numpy as np

from danesfield.rpc import *

import gdal
import ogr

def parse_raytheon_rpc_file(fp):
    """Parse the Raytheon RPC file format from an open file pointer
    """
    def parse_rational_poly(fp):
        """Parse coefficients for a two polynomials from the file stream
        """
        coeff = numpy.zeros((2, 20), dtype='float64')
        idx = 0
        powers = True
        # The expected exponent order matrix.  Currently we only support this
        # default.  If what is in the file doesn't match, raise an exception
        exp_exp_mat = [[0, 0, 0, 1], [1, 0, 0, 1], [0, 1, 0, 1], [0, 0, 1, 1],
                       [1, 1, 0, 1], [1, 0, 1, 1], [0, 1, 1, 1], [2, 0, 0, 1],
                       [0, 2, 0, 1], [0, 0, 2, 1], [1, 1, 1, 1], [3, 0, 0, 1],
                       [1, 2, 0, 1], [1, 0, 2, 1], [2, 1, 0, 1], [0, 3, 0, 1],
                       [0, 1, 2, 1], [2, 0, 1, 1], [0, 2, 1, 1], [0, 0, 3, 1]]
        for line in fp:
            if line.strip() == '20':
                data = []
                for i in range(20):
                    data.append(fp.next())
                if powers:
                    powers = False
                    exp_mat = numpy.array([d.split() for d in data],
                                          dtype='int')
                    if not numpy.array_equal(exp_mat, exp_exp_mat):
                        raise ValueError
                else:
                    powers = True
                    coeff[idx, :] = numpy.array(data, dtype='float64')
                    idx = idx + 1
                if idx > 1:
                    break
        return coeff

    rpc = RPCModel()
    for line in fp:
        if line.startswith('# uvOffset_'):
            line = fp.next()
            rpc.image_offset = numpy.array(line.split(), dtype='float64')
        if line.startswith('# uvScale_'):
            line = fp.next()
            rpc.image_scale = numpy.array(line.split(), dtype='float64')
        if line.startswith('# xyzOffset_'):
            line = fp.next()
            rpc.world_offset = numpy.array(line.split(), dtype='float64')
        if line.startswith('# xyzScale_'):
            line = fp.next()
            rpc.world_scale = numpy.array(line.split(), dtype='float64')
        if line.startswith('# u=sample'):
            rpc.coeff[0:2, :] = parse_rational_poly(fp)
        if line.startswith('# v=line'):
            rpc.coeff[2:4, :] = parse_rational_poly(fp)
    return rpc


def gdal_get_transform(src_image):
    geo_trans = src_image.GetGeoTransform()
    if geo_trans==(0.0, 1.0, 0.0, 0.0, 0.0, 1.0):
        geo_trans = gdal.GCPsToGeoTransform(src_image.GetGCPs())
    return geo_trans


def gdal_get_projection(src_image):
    projection = src_image.GetProjection()
    if projection == '':
        projection = src_image.GetGCPProjection()
    return projection


def image_to_array(i):
    """
    Converts a Python Imaging Library array to a
    gdalnumeric image.
    """
    a = gdalnumeric.numpy.fromstring(i.tobytes(), 'b')
    a.shape = i.im.size[1], i.im.size[0]
    return a


def world_to_pixel_poly(rpc, geometry):
    """
    Uses a gdal geomatrix (gdal.GetGeoTransform()) to calculate
    the pixel location of a geospatial coordinate
    """
    pixelRing = ogr.Geometry(ogr.wkbLinearRing)
    geoRing = geometry.GetGeometryRef(0)
    numPoints = geoRing.GetPointCount()
    for p in range(numPoints):
        point = numpy.array(map(float, geoRing.GetPoint(p)))
        pixel, line = rpc.project(point)
        pixelRing.AddPoint(pixel, line)
    pixelPoly = ogr.Geometry(ogr.wkbPolygon)
    pixelPoly.AddGeometry(pixelRing)
    return pixelPoly


def world_to_pixel(geoMatrix, x, y):
    """
    Uses a gdal geomatrix (gdal.GetGeoTransform()) to calculate
    the pixel location of a geospatial coordinate
    """
    tX = x - geoMatrix[0]
    tY = y - geoMatrix[3]
    a = geoMatrix[1]
    b = geoMatrix[2]
    c = geoMatrix[4]
    d = geoMatrix[5]
    div = 1.0 / (a * d - b * c)
    pixel = int((tX * d - tY * b) * div)
    line = int((tY * a - tX * c) * div)
    return (pixel, line)


def read_raytheon_RPC(rpc_path, img_file):
    # image file name for pattern of raytheon RPC
    file_no_ext = os.path.splitext(img_file)[0]
    rpc_file = rpc_path + 'GRA_' + file_no_ext + '.up.rpc'
    if os.path.isfile(rpc_file) is False:
        rpc_file = rpc_file = rpc_path + 'GRA_' + file_no_ext + '_0.up.rpc'
        if os.path.isfile(rpc_file) is False:
            return None

    with open(rpc_file, 'r') as f:
        return parse_raytheon_rpc_file(f)

src_root_dir = '/home/david/Desktop/test_crop/'
dst_root_dir = '/home/david/Desktop/test_crop/out/'

corrected_rpc_dir = '/videonas2/fouo/data_working/CORE3D-Phase1A/AOIs/D1_WPAFB/P3D/D1_ptclds_WPAFB_museum/ba_updated_rpcs/'

# pad the crop by the following percentage in width and height
# This value should be 1 >= padding_percentage > 0
# Setting padding_percentage to 0 disables padding.
padding_percentage = 0

### jacksonville
#ul_lon = -81.67078466333165
#ul_lat = 30.31698808384777

#ur_lon = -81.65616946309449
#ur_lat = 30.31729872444624

#lr_lon = -81.65620275072482
#lr_lat = 30.329923847788603

#ll_lon = -81.67062242425624
#ll_lat = 30.32997669492018

### ucsd
#ul_lon = -117.24298768132505
#ul_lat = 32.882791370856857

#ur_lon = -117.24296375496185
#ur_lat = 32.874021450913411

#lr_lon = -117.2323749640905
#lr_lat = 32.874041569804469

#ll_lon = -117.23239784772379
#ll_lat = 32.882811496466012

### wpafb D1
ul_lon = -84.11236693243779
ul_lat = 39.77747025512961

ur_lon = -84.10530109439955
ur_lat = 39.77749705975315

lr_lon = -84.10511182729961
lr_lat = 39.78290042788092

ll_lon = -84.11236485416471
ll_lat = 39.78287156225952

### wpafb D2
#ul_lon = -84.08847226672408
#ul_lat = 39.77650841377968

#ur_lon = -84.07992142333644
#ur_lat = 39.77652166058358

#lr_lon = -84.07959205694203
#lr_lat = 39.78413758747398

#ll_lon = -84.0882028871317
#ll_lat = 39.78430009793551

# Apply the padding if the value of padding_percentage > 0
if padding_percentage > 0:
    ulon_pad = ((ur_lon - ul_lon)*padding_percentage)/2
    llon_pad = ((lr_lon - ll_lon)*padding_percentage)/2
    ul_lon = ul_lon - ulon_pad
    ur_lon = ur_lon + ulon_pad
    lr_lon = lr_lon + llon_pad
    ll_lon = ll_lon - llon_pad
    llat_pad = ((ll_lat - ul_lat)*padding_percentage)/2
    rlat_pad = ((lr_lat - ur_lat)*padding_percentage)/2
    ul_lat = ul_lat - llat_pad
    ur_lat = ur_lat - rlat_pad
    lr_lat = lr_lat + rlat_pad
    ll_lat = ll_lat + llat_pad

working_dst_dir = dst_root_dir

for root, dirs, files in os.walk(src_root_dir):
    for file_ in files:
        new_root = root.replace(src_root_dir, dst_root_dir)
        if not os.path.exists(new_root):
            os.makedirs(new_root)

        ext = os.path.splitext(file_)[-1].lower()
        if ext != ".ntf":
            continue

        try:
            src_img_file = os.path.join(root, file_)
            dst_img_file = os.path.join(new_root, file_)
            dst_file_no_ext = os.path.splitext(dst_img_file)[0]
            dst_img_file = dst_file_no_ext + ".tif"

            src_image = gdal.Open(src_img_file, gdalconst.GA_ReadOnly)
            geo_trans = gdal_get_transform(src_image)

            nodata_values = []
            nodata = 0
            for i in range(src_image.RasterCount):
                nodata_value = src_image.GetRasterBand(i+1).GetNoDataValue()
                if not nodata_value:
                    nodata_value = nodata
                nodata_values.append(nodata_value)

            polygon_json = {"type":
                                "Polygon", "coordinates":
                                [[[ul_lon, ul_lat], [ur_lon, ur_lat],
                                  [lr_lon, lr_lat], [ll_lon, ll_lat],
                                  [ul_lon, ul_lat]]]}

            polygon_json = json.dumps(polygon_json)
            poly = ogr.CreateGeometryFromJson(polygon_json)
            min_x, max_x, min_y, max_y = poly.GetEnvelope()
            rpc_md = src_image.GetMetadata('RPC')
            rpc = rpc_from_gdal_dict(rpc_md)
            if (corrected_rpc_dir):
                updated_rpc = read_raytheon_RPC(corrected_rpc_dir, file_)
                if updated_rpc is None:
                    print ('No RPC file exists for image file: ' + src_img_file)
                else:
                    rpc = updated_rpc
                    rpc_md = rpc_to_gdal_dict(updated_rpc)

            pixelPoly = world_to_pixel_poly(rpc, poly)

            ul_x, lr_x, ul_y, lr_y = map(int, pixelPoly.GetEnvelope())
            ul_x = max(0, ul_x)
            ul_y = max(0, ul_y)
            lr_x = min(src_image.RasterXSize - 1, lr_x)
            lr_y = min(src_image.RasterYSize - 1, lr_y)

            samp_off = rpc_md['SAMP_OFF']
            samp_off = float(samp_off) - ul_x
            rpc_md['SAMP_OFF'] = str(samp_off)

            line_off = rpc_md['LINE_OFF']
            line_off = float(line_off) - ul_y
            rpc_md['LINE_OFF'] = str(line_off)

            # Calculate the pixel size of the new image
            # Constrain the width and height to the bounds of the image
            px_width = int(lr_x - ul_x + 1)
            if px_width + ul_x > src_image.RasterXSize - 1:
                px_width = int(src_image.RasterXSize - ul_x - 1)

            px_height = int(lr_y - ul_y + 1)
            if px_height + ul_y > src_image.RasterYSize - 1:
                px_height = int(src_image.RasterYSize - ul_y - 1)

            # We've constrained x & y so they are within the image.
            # If the width or height ends up negative at this point,
            # the AOI is completely outside the image
            if px_width < 0 or px_height < 0:
                continue

            # Load the source data as a gdalnumeric array
            clip = src_image.ReadAsArray(ul_x, ul_y, px_width, px_height)
            src_dtype = clip.dtype

            # Create a new geomatrix for the image
            geo_trans = list(geo_trans)
            geo_trans[0] = min_x
            geo_trans[3] = max_y

            # Map points to pixels for drawing the
            # boundary on a blank 8-bit,
            # black and white, mask image.
            raster_poly = Image.new("L", (px_width, px_height), 1)
            rasterize = ImageDraw.Draw(raster_poly)
            geometry_count = poly.GetGeometryCount()
            for i in range(0, geometry_count):
                points = []
                pixels = []
                pts = poly.GetGeometryRef(i)
                if pts.GetPointCount() == 0:
                    pts = pts.GetGeometryRef(0)
                for p in range(pts.GetPointCount()):
                    points.append((pts.GetX(p), pts.GetY(p)))
                for p in points:
                    pixels.append(world_to_pixel(geo_trans, p[0], p[1]))
                rasterize.polygon(pixels, 0)

            # create output raster
            raster_band = src_image.GetRasterBand(1)
            output_driver = gdal.GetDriverByName('MEM')

            # In the event we have multispectral images,
            # shift the shape dimesions we are after,
            # since position 0 will be the number of bands
            clip_shp_0 = clip.shape[0]
            clip_shp_1 = clip.shape[1]
            if clip.ndim > 2:
                clip_shp_0 = clip.shape[1]
                clip_shp_1 = clip.shape[2]

            output_dataset = output_driver.Create(
                '', clip_shp_1, clip_shp_0,
                src_image.RasterCount, raster_band.DataType)
            output_dataset.SetGeoTransform(geo_trans)
            output_dataset.SetProjection(gdal_get_projection(src_image))

            # Copy All metadata data from src to dst
            # domains = src_image.GetMetadataDomainList()
            # for tag in domains:
            #     md = src_image.GetMetadata(tag)
            #     if md:
            #         output_dataset.SetMetadata(md, tag)

            # write out the rpc_md that we modified above
            output_dataset.SetMetadata(rpc_md, 'RPC')
            gdalnumeric.CopyDatasetInfo(src_image, output_dataset,
                                        xoff=ul_x, yoff=ul_y)

            bands = src_image.RasterCount
            if bands > 1:
                for i in range(bands):
                    outBand = output_dataset.GetRasterBand(i + 1)
                    outBand.SetNoDataValue(nodata_values[i])
                    outBand.WriteArray(clip[i])
            else:
                outBand = output_dataset.GetRasterBand(1)
                outBand.SetNoDataValue(nodata_values[0])
                outBand.WriteArray(clip)

            if dst_img_file:
                output_driver = gdal.GetDriverByName('GTiff')
                outfile = output_driver.CreateCopy(dst_img_file, output_dataset, False)
                logger.debug(str(outfile))
                outfile = None

        except:
            print ('Problem cropping image: ' + src_img_file)
            print (traceback.format_exc())


'''


import gdal
import ogr
import osr
from PIL import Image, ImageDraw
import gdalnumeric

from gaia.geo.processes_raster import *
from gaia.geo.geo_inputs import *
from gaia.geo.rpc import *


def gdal_get_transform(src_image):
    geo_trans = src_image.GetGeoTransform()
    if geo_trans==(0.0, 1.0, 0.0, 0.0, 0.0, 1.0):
        geo_trans = gdal.GCPsToGeoTransform(src_image.GetGCPs())
    return geo_trans


def gdal_get_projection(src_image):
    projection = src_image.GetProjection()
    if projection == '':
        projection = src_image.GetGCPProjection()
    return projection


def image_to_array(i):
    """
    Converts a Python Imaging Library array to a
    gdalnumeric image.
    """
    a = gdalnumeric.numpy.fromstring(i.tobytes(), 'b')
    a.shape = i.im.size[1], i.im.size[0]
    return a


def world_to_pixel_poly(rpc_dict, geometry):
    """
    Uses a gdal geomatrix (gdal.GetGeoTransform()) to calculate
    the pixel location of a geospatial coordinate
    """
    pixelRing = ogr.Geometry(ogr.wkbLinearRing)
    geoRing = geometry.GetGeometryRef(0)
    numPoints = geoRing.GetPointCount()
    rpc = rpc_from_gdal_dict(rpc_dict)
    for p in range(numPoints):
        point = np.array(map(float, geoRing.GetPoint(p)))
        pixel, line = rpc.project(point)
        pixelRing.AddPoint(pixel, line)
    pixelPoly = ogr.Geometry(ogr.wkbPolygon)
    pixelPoly.AddGeometry(pixelRing)
    return pixelPoly

def world_to_pixel(geoMatrix, x, y):
    """
    Uses a gdal geomatrix (gdal.GetGeoTransform()) to calculate
    the pixel location of a geospatial coordinate
    """
    tX = x - geoMatrix[0]
    tY = y - geoMatrix[3]
    a = geoMatrix[1]
    b = geoMatrix[2]
    c = geoMatrix[4]
    d = geoMatrix[5]
    div = 1.0 / (a * d - b * c)
    pixel = int((tX * d - tY * b) * div)
    line = int((tY * a - tX * c) * div)
    return (pixel, line)


padding_percentage = 1

### jacksonville
ul_lon = -81.67078466333165
ul_lat = 30.31698808384777

ur_lon = -81.65616946309449
ur_lat = 30.31729872444624

lr_lon = -81.65620275072482
lr_lat = 30.329923847788603

ll_lon = -81.67062242425624
ll_lat = 30.32997669492018


# Apply the padding if the value of padding_percentage > 0
if padding_percentage > 0:
    ulon_pad = ((ur_lon - ul_lon)*padding_percentage)/2
    llon_pad = ((lr_lon - ll_lon)*padding_percentage)/2
    ul_lon = ul_lon - ulon_pad
    ur_lon = ur_lon + ulon_pad
    lr_lon = lr_lon + llon_pad
    ll_lon = ll_lon - llon_pad
    llat_pad = ((ll_lat - ul_lat)*padding_percentage)/2
    rlat_pad = ((lr_lat - ur_lat)*padding_percentage)/2
    ul_lat = ul_lat - llat_pad
    ur_lat = ur_lat - rlat_pad
    lr_lat = lr_lat + rlat_pad
    ll_lat = ll_lat + llat_pad




src_img_file = '/videonas2/fouo/data_golden/CORE3D-Phase1A/performer_data/performer_source_data/jacksonville/satellite_imagery/WV3/PAN/18FEB16WV031200016FEB18164007-P1BS-501504472090_01_P001_________AAE_0AAAAABPABP0.NTF'

dst_img_file = '/home/david/Desktop/temp.tif'


globaltemp = RasterFileIO(uri=src_img_file)
polygonio1 = FeatureIO(features=
                       [{"geometry":
                             {"type":
                                  "Polygon", "coordinates":
                                  [[[ul_lon, ul_lat], [ur_lon, ur_lat],
                                    [lr_lon, lr_lat], [ll_lon, ll_lat],
                                    [ul_lon, ul_lat]]]},
                         "properties":
                             {"id":
                                  "North polygon"}} ])

subset_pr = SubsetProcess(inputs=[globaltemp, polygonio1])


raster, clip = globaltemp, polygonio1
raster_img = raster.read()
clip_df = clip.read(epsg=raster.get_epsg())
clip_json = clip_df.geometry.unary_union.__geo_interface__
raster_input = raster_img
polygon_json = clip_json
nodata=0

src_image = raster_input
raster_output=dst_img_file

# Load as a gdal image to get geotransform
# (world file) info
geo_trans = gdal_get_transform(src_image)
nodata_values = []
for i in range(src_image.RasterCount):
    nodata_value = src_image.GetRasterBand(i+1).GetNoDataValue()
    if not nodata_value:
        nodata_value = nodata
    nodata_values.append(nodata_value)

# Create an OGR layer from a boundary GeoJSON geometry string
polygon_json = json.dumps(polygon_json)
poly = ogr.CreateGeometryFromJson(polygon_json)


min_x, max_x, min_y, max_y = poly.GetEnvelope()
rpc_md = src_image.GetMetadata('RPC')
pixelPoly = world_to_pixel_poly(rpc_md, poly)

# Convert the layer extent to image pixel coordinates
ul_x, lr_x, ul_y, lr_y = pixelPoly.GetEnvelope()
ul_x, lr_x, ul_y, lr_y = int(ul_x), int(lr_x), int(ul_y), int(lr_y)


if ul_x < 0:
    ul_x = 0

if ul_y < 0:
    ul_y = 0

if lr_x > src_image.RasterXSize:
    lr_x = src_image.RasterXSize

if lr_y > src_image.RasterYSize:
    lr_y = src_image.RasterYSize

# Calculate the pixel size of the new image
# Constrain the width and height to the bounds of the image
px_width = int(lr_x - ul_x)
if px_width + ul_x > src_image.RasterXSize :
    px_width = int(src_image.RasterXSize - ul_x)

px_height = int(lr_y - ul_y)
if px_height + ul_y > src_image.RasterYSize :
    px_height = int(src_image.RasterYSize - ul_y)

# Load the source data as a gdalnumeric array
clip = src_image.ReadAsArray(ul_x, ul_y, px_width, px_height)
src_dtype = clip.dtype

# create pixel offset to pass to new image Projection info
xoffset = ul_x
yoffset = ul_y

# Create a new geomatrix for the image
geo_trans = list(geo_trans)
geo_trans[0] = min_x
geo_trans[3] = max_y

# Map points to pixels for drawing the
# boundary on a blank 8-bit,
# black and white, mask image.
raster_poly = Image.new("L", (px_width, px_height), 1)
rasterize = ImageDraw.Draw(raster_poly)
geometry_count = poly.GetGeometryCount()
for i in range(0, geometry_count):
    points = []
    pixels = []
    pts = poly.GetGeometryRef(i)
    if pts.GetPointCount() == 0:
        pts = pts.GetGeometryRef(0)
    for p in range(pts.GetPointCount()):
        points.append((pts.GetX(p), pts.GetY(p)))
    for p in points:
        pixels.append(world_to_pixel(geo_trans, p[0], p[1]))
    rasterize.polygon(pixels, 0)

mask = image_to_array(raster_poly)

# Clip the image using the mask
#clip = gdalnumeric.numpy.choose(
#    mask, (clip, nodata_value)).astype(src_dtype)

# create output raster
raster_band = raster_input.GetRasterBand(1)
output_driver = gdal.GetDriverByName('MEM')

# In the event we have multispectral images, shift the shape dimesions we are after,
# since position 0 will be the number of bands
clip_shp_0 = clip.shape[0]
clip_shp_1 = clip.shape[1]
if clip.ndim > 2:
    clip_shp_0 = clip.shape[1]
    clip_shp_1 = clip.shape[2]

output_dataset = output_driver.Create(
    '', clip_shp_1, clip_shp_0,
    raster_input.RasterCount, raster_band.DataType)
output_dataset.SetGeoTransform(geo_trans)
output_dataset.SetProjection(gdal_get_projection(raster_input))


#Copy All metadata data from src to dst
domains = src_image.GetMetadataDomainList()
for tag in domains:
    md = src_image.GetMetadata(tag)
    if md:
        output_dataset.SetMetadata(md, tag)


gdalnumeric.CopyDatasetInfo(raster_input, output_dataset,
                            xoff=xoffset, yoff=yoffset)


bands = raster_input.RasterCount
if bands > 1:
    for i in range(bands):
        outBand = output_dataset.GetRasterBand(i + 1)
        outBand.SetNoDataValue(nodata_values[i])
        outBand.WriteArray(mask[i])
else:
    outBand = output_dataset.GetRasterBand(1)
    outBand.SetNoDataValue(nodata_values[0])
    outBand.WriteArray(mask)

if raster_output:
    output_driver = gdal.GetDriverByName('GTiff')
    outfile = output_driver.CreateCopy(raster_output, output_dataset, False)
    logger.debug(str(outfile))
    outfile = None

'''
