#!/usr/bin/env python3
"""Downloads and meshes HUC based on hydrography data."""

import os,sys
import numpy as np
from matplotlib import pyplot as plt
import shapely
import logging

import workflow
import workflow.ui
import workflow.source_list

def get_args():
    # set up parser
    parser = workflow.ui.get_basic_argparse(__doc__+'\n\n'+workflow.source_list.__doc__)
    workflow.ui.huc_arg(parser)
    workflow.ui.outmesh_args(parser)
    workflow.ui.center_options(parser)

    workflow.ui.simplify_options(parser)
    workflow.ui.refine_options(parser)

    data_ui = parser.add_argument_group('Data Sources')
    workflow.ui.huc_source_options(data_ui)
    workflow.ui.hydro_source_options(data_ui)
    workflow.ui.dem_source_options(data_ui)

    # parse args, log
    return parser.parse_args()

def mesh_hucs(args):
    workflow.ui.setup_logging(args.verbosity, args.logfile)
    sources = workflow.source_list.get_sources(args)

    logging.info("")
    logging.info("Meshing HUC: {}".format(args.HUC))
    logging.info("="*30)
    logging.info('Target projection: "{}"'.format(args.projection['init']))
    
    # collect data
    huc, centroid = workflow.get_split_form_hucs(sources['HUC'], args.HUC, crs=args.projection, centering=args.center)
    rivers, centroid = workflow.get_rivers_by_bounds(sources['hydrography'], huc.polygon(0).bounds, args.projection, args.HUC, centering=centroid)
    rivers = workflow.simplify_and_prune(huc, rivers, args)
    
    # make 2D mesh
    mesh_points2, mesh_tris = workflow.triangulate(huc, rivers, args)

    # elevate to 3D
    if args.center:
        mesh_points2_uncentered = mesh_points2 + np.expand_dims(np.array(centroid.coords[0]),0)
    else:
        mesh_points2_uncentered = mesh_points2

    dem_profile, dem = workflow.get_dem_on_shape(sources['DEM'], huc.polygon(0), workflow.conf.default_crs())
    mesh_points3_uncentered = workflow.elevate(mesh_points2_uncentered, dem, dem_profile)

    if args.center:
        mesh_points3 = np.empty(mesh_points3_uncentered.shape,'d')
        mesh_points3[:,0:2] = mesh_points2
        mesh_points3[:,2] = mesh_points3_uncentered[:,2]
    else:
        mesh_points3 = mesh_points3_uncentered

    return centroid, huc, rivers, (mesh_points3, mesh_tris)

def plot(args, hucs, rivers, triangulation):
    mesh_points3, mesh_tris = triangulation
    if args.verbosity > 0:    
        fig = plt.figure(figsize=(4,5))
        ax = fig.add_subplot(111)
        mp = workflow.plot.triangulation(mesh_points3, mesh_tris, linewidth=0, color='elevation')
        #fig.colorbar(mp, orientation="horizontal", pad=0.1)
        workflow.plot.hucs(hucs, 'k', linewidth=0.7)
        workflow.plot.rivers(rivers, color='blue', linewidth=0.5)
        ax.set_aspect('equal', 'datalim')
        ax.set_xlabel('')
        ax.set_xticklabels([round(0.001*tick) for tick in ax.get_xticks()])
        plt.ylabel('')
        ax.set_yticklabels([round(0.001*tick) for tick in ax.get_yticks()])
        plt.savefig('my_mesh')

def save(args, centroid, triangulation):
    mesh_points3, mesh_tris = triangulation
    metadata_lines = ['Mesh of HUC: %s including all HUC 12 boundaries and hydrography.'%args.HUC,
                      '',
                      '  coordinate system = epsg:%04i'%(workflow.conf.rcParams['epsg']),
                      ]

    if args.center:
        metadata_lines.append('  centered to: %g, %g'%centroid.coords[0])
    metadata_lines.extend(['',
                           'Mesh generated by workflow mesh_hucs.py script.',
                           '',
                           workflow.utils.get_git_revision_hash(),
                           '',
                           'with calling sequence:',
                           '  '+' '.join(sys.argv)])

    workflow.save(args.output_file, mesh_points3, mesh_tris, '\n'.join(metadata_lines))
        

if __name__ == '__main__':
    try:
        args = get_args()
        centroid, hucs, rivers, triangulation = mesh_hucs(args)
        plot(args, hucs, rivers, triangulation)
        save(args, centroid, triangulation)
        logging.info("SUCESS")
        plt.show()
        sys.exit(0)
    except KeyboardInterrupt:
        logging.error("Keyboard Interupt, stopping.")
        sys.exit(0)
    except Exception as err:
        logging.error('{}'.format(str(err)))
        sys.exit(1)
        
