"""Collection of common functionality across multiple scripts in bin."""
import os,sys
from matplotlib import pyplot as plt
import vtk_io
import logging
import rasterio.transform
import shapely
import numpy as np
import cartopy.feature

import workflow.plot
import workflow.conf
import workflow.utils


def plot_with_triangulation(args, hucs, rivers, triangulation,
                            shape_color='k', river_color='white',
                            fig=None, ax=None):
    logging.info('Plotting')
    logging.info('--------')

    # get a figure and axis
    if fig is None:
        fig = plt.figure(figsize=args.figsize)
    if ax is None:
        ax = workflow.plot.get_ax(args.projection, fig=fig)
        
    if triangulation is not None:
        mesh_points3, mesh_tris = triangulation
        mp = workflow.plot.triangulation(mesh_points3, mesh_tris, args.projection,
                                         color='elevation', ax=ax, linewidth=0)
        #fig.colorbar(mp, orientation="horizontal", pad=0.1)
    if rivers is not None:
        workflow.plot.rivers(rivers, args.projection, river_color, ax, linewidth=0.5)

    if hucs is not None:
        workflow.plot.hucs(hucs, args.projection, shape_color, ax, linewidth=.7)

    ax.set_aspect('equal', 'datalim')
    return fig, ax
    
def plot_with_dem(args, hucs, rivers, dem, profile,
                  shape_color='k', river_color='white',
                  cb=True, cb_label='elevation [m]', vmin=None, vmax=None,
                  fig=None, ax=None):

    logging.info('Plotting')
    logging.info('--------')

    # get a figure and axis
    if fig is None:
        fig = plt.figure(figsize=args.figsize)
    if ax is None:
        ax = workflow.plot.get_ax(args.projection, fig=fig)

    # get a plot extent
    if args.extent is None:
        args.extent = hucs.exterior().bounds

        if args.pad_fraction is not None:
            if len(args.pad_fraction) == 1:
                dxp = (args.extent[2] - args.extent[0]) * args.pad_fraction[0]
                dxm = dxp
                dym = dxp
                dyp = dxp
            elif len(args.pad_fraction) == 2:
                dxp = (args.extent[2] - args.extent[0]) * args.pad_fraction[0]
                dxm = dxp
                dyp = (args.extent[3] - args.extent[1]) * args.pad_fraction[1]
                dym = dyp
            elif len(args.pad_fraction) == 4:
                dxm = (args.extent[2] - args.extent[0]) * args.pad_fraction[0]
                dym = (args.extent[3] - args.extent[1]) * args.pad_fraction[1]
                dxp = (args.extent[2] - args.extent[0]) * args.pad_fraction[2]
                dyp = (args.extent[3] - args.extent[1]) * args.pad_fraction[3]
            else:
                raise ValueError('Option: --pad-fraction must be of length 1, 2, or 4')

            args.extent = [args.extent[0] - dxm, args.extent[1] - dym,
                           args.extent[2] + dxp, args.extent[3] + dyp]

    logging.info('plot extent: {}'.format(args.extent))
            
    # continents
    if args.basemap:
        land = cartopy.feature.NaturalEarthFeature('physical', 'land',
                                                   args.basemap_resolution,
                                                   edgecolor='face',
                                                   facecolor=cartopy.feature.COLORS['land'],
                                                   zorder=0)
        ax.add_feature(land)
        ocean = cartopy.feature.NaturalEarthFeature('physical', 'ocean',
                                                    args.basemap_resolution,
                                                    edgecolor='face',
                                                    facecolor=cartopy.feature.COLORS['water'],
                                                    zorder=2)
        ax.add_feature(ocean)
        
    # plot the raster
    # -- pad the raster to have the same extent
    if dem is not None:
        mappable = workflow.plot.dem(profile, dem, ax, vmin, vmax)
        if args.basemap:
            mappable.set_zorder(1)
        if cb:
            cb = fig.colorbar(mappable, orientation="horizontal", pad=0)
            cb.set_label(cb_label)

    # plot HUCs and rivers on top
    if rivers is not None:
        workflow.plot.rivers(rivers, args.projection, river_color, ax, linewidth=0.5, zorder=3)

    if hucs is not None:
        workflow.plot.hucs(hucs, args.projection, shape_color, ax, linewidth=.7, zorder=4)

    ax.set_xlim(args.extent[0], args.extent[2])
    ax.set_ylim(args.extent[1], args.extent[3])
    ax.set_aspect('equal', 'box')
    ax.set_title(args.title)
    return fig, ax

        
def save(args, triangulation):
    mesh_points3, mesh_tris = triangulation
    if hasattr(args, 'HUC'):
        metadata_lines = ['Mesh of HUC: %s'%args.HUC,
                          '',
                          '  coordinate system = epsg:{}'.format(args.projection),
                         ]
    else:
        metadata_lines = ['Mesh of shape: %s'%args.input_file,
                          '',
                          '  coordinate system = epsg:{}'.format(args.projection),
                         ]

    metadata_lines.extend(['',
                           'Mesh generated by workflow mesh_hucs.py script.',
                           '',
                           workflow.conf.get_git_revision_hash(),
                           '',
                           'with calling sequence:',
                           '  '+' '.join(sys.argv)])

    logging.info("")
    logging.info("File I/O")
    logging.info("-"*30)
    logging.info("Saving mesh: %s"%args.output_file)
    vtk_io.write(args.output_file, mesh_points3, {'triangle':mesh_tris})

    logging.info("Saving README: %s"%args.output_file+'.readme') 
    with open(args.output_file+'.readme','w') as fid:
        fid.write('\n'.join(metadata_lines))
