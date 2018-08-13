import math
import copy
import logging
import numpy as np
from matplotlib import pyplot as plt
import scipy.spatial
import itertools

import shapely.geometry

import workflow.conf
import workflow.utils
import workflow.tree
import workflow.hucs
import workflow.plot


def snap(hucs, rivers, tol=0.1, tol_triples=None):
    """Snap HUCs to rivers."""
    assert(type(hucs) is workflow.hucs.HUCs)
    assert(type(rivers) is list)
    assert(all(workflow.tree.is_consistent(river) for river in rivers))
    list(hucs.polygons())

    if len(rivers) is 0:
        return True

    if tol_triples is None:
        tol_triples = tol

    # snap boundary triple junctions to river endpoints
    logging.info(" Snapping polygon segment boundaries to river endpoints")
    snap_polygon_endpoints(hucs, rivers, tol_triples)
    if not all(workflow.tree.is_consistent(river) for river in rivers):
        logging.info("  ...resulted in inconsistent rivers!")
        return False
    try:
        list(hucs.polygons())
    except AssertionError:
        logging.info("  ...resulted in inconsistent HUCs")
        return False
    
    # snap endpoints of all rivers to the boundary if close
    # note this is a null-op on cases dealt with above
    logging.info(" Snapping river endpoints to the polygon")
    for tree in rivers:
        snap_endpoints(tree, hucs, tol)
    if not all(workflow.tree.is_consistent(river) for river in rivers):
        logging.info("  ...resulted in inconsistent rivers!")
        return False
    try:
        list(hucs.polygons())
    except AssertionError:
        logging.info("  ...resulted in inconsistent HUCs")
        return False

    # # deal with intersections
    # logging.info(" Cutting at crossings")
    # snap_crossings(hucs, rivers, tol)
    # consistent = all(workflow.tree.is_consistent(river) for river in rivers)
    # if not consistent:
    #     logging.info("  ...resulted in inconsistent rivers!")
    #     return False
    # try:
    #     list(hucs.polygons())
    # except AssertionError:
    #     logging.info("  ...resulted in inconsistent HUCs")
    #     return False
    return True

def _snap_and_cut(point, line, tol=0.1):
    """Determine the closest point to a line and, if it is within tol of
    the line, cut the line at that point and snapping the endpoints as
    needed.
    """
    if workflow.utils.in_neighborhood(shapely.geometry.Point(point), line, tol):
        nearest_p = workflow.utils.nearest_point(line, point)
        dist = workflow.utils.distance(nearest_p, point)
        if dist < tol:
            if dist < 1.e-7:
                # filter case where the point is already there
                if any(workflow.utils.close(point, c) for c in line.coords):
                    return None 
            return nearest_p
    return None

def _snap_crossing(hucs, river_node, tol=0.1):
    """Snap a single river node"""
    r = river_node.segment
    for b,spine in hucs.intersections.items():
        for s,seg_handle in spine.items():
            seg = hucs.segments[seg_handle]

            if seg.intersects(r):
                new_spine = workflow.utils.cut(seg, r, tol)
                try:
                    new_rivers = workflow.utils.cut(r, seg, tol)
                except RuntimeError as err:
                    plt.figure()
                    workflow.plot.hucs(hucs,color='gray')
                    plt.plot(seg.xy[0], seg.xy[1], 'b-+')
                    plt.plot(r.xy[0], r.xy[1], 'r-x')
                    plt.show()
                    raise err
                
                river_node.segment = new_rivers[-1]
                if len(new_rivers) > 1:
                    assert(len(new_rivers) == 2)
                    river_node.inject(workflow.tree.Tree(new_rivers[0]))

                hucs.segments[seg_handle] = new_spine[0]
                if len(new_spine) > 1:
                    assert(len(new_spine) == 2)
                    new_handle = hucs.segments.add(new_spine[1])
                    spine.add(new_handle)
                break
                    
def snap_crossings(hucs, rivers, tol=0.1):
    """Snaps HUC boundaries and rivers to crossings."""
    for tree in rivers:
        for river_node in tree.preOrder():
            _snap_crossing(hucs, river_node, tol)
    
def snap_polygon_endpoints(hucs, rivers, tol=0.1):
    """Snaps the endpoints of HUC segments to endpoints of rivers."""
    # make the kdTree of endpoints of all rivers
    coords1 = np.array([r.coords[-1] for tree in rivers for r in tree.dfs()])
    coords2 = np.array([r.coords[0] for tree in rivers for r in tree.leaves()])
    coords = np.concatenate([coords1, coords2], axis=0)
    kdtree = scipy.spatial.cKDTree(coords)

    # for each segment of the HUC spine, find the river outlet that is
    # closest.  If within tolerance, move it
    for seg_handle, seg in hucs.segments.items():
        # check point 0
        dists,inds = kdtree.query(np.array([seg.coords[0],seg.coords[-1]]))
        if dists.min() < tol:
            new_seg = list(seg.coords)
            if dists[0] < tol:
                new_seg[0] = coords[inds[0]]
                logging.debug("  Moving HUC segment point 0 to river at %r"%list(new_seg[0]))
            if dists[1] < tol:
                new_seg[-1] = coords[inds[1]]
                logging.debug("  Moving HUC segment point -1 to river at %r"%list(new_seg[-1]))
            hucs.segments[seg_handle] = shapely.geometry.LineString(new_seg)

def snap_endpoints(tree, hucs, tol=0.1):
    """Snap river endpoints to huc segments and insert that point into
    the boundary.

    Note this is O(n^2), and could be made more efficient.
    """
    to_add = []
    for node in tree.preOrder():
        river = node.segment
        for b,component in itertools.chain(hucs.boundaries.items(), hucs.intersections.items()):

            # note, this is done in two stages to allow it deal with both endpoints touching
            for s,seg_handle in component.items():
                seg = hucs.segments[seg_handle]
                #logging.debug("SNAP P0:")
                #logging.debug("  huc seg: %r"%seg.coords[:])
                #logging.debug("  river: %r"%river.coords[:])
                altered = False
                new_coord = _snap_and_cut(river.coords[0], seg, tol)
                if new_coord is not None:
                    logging.info("  - snapped river: %r to %r"%(river.coords[0], new_coord))

                    # remove points that are closer
                    coords = list(river.coords)
                    done = False
                    while len(coords) > 2 and workflow.utils.distance(new_coord, coords[1]) < \
                          workflow.utils.distance(new_coord, coords[0]):
                        coords.pop(0)
                    coords[0] = new_coord
                    river = shapely.geometry.LineString(coords)
                    node.segment = river
                    to_add.append((seg_handle, component, 0, node))
                    break

            # second stage
            for s,seg_handle in component.items():
                seg = hucs.segments[seg_handle]
                # logging.debug("SNAP P1:")
                # logging.debug("  huc seg: %r"%seg.coords[:])
                # logging.debug("  river: %r"%river.coords[:])
                altered = False
                new_coord = _snap_and_cut(river.coords[-1], seg, tol)
                if new_coord is not None:
                    logging.info("  - snapped river: %r to %r"%(river.coords[-1], new_coord))

                    # remove points that are closer
                    coords = list(river.coords)
                    done = False
                    while len(coords) > 2 and workflow.utils.distance(new_coord, coords[-2]) < \
                          workflow.utils.distance(new_coord, coords[-1]):
                        coords.pop(-1)
                    coords[-1] = new_coord
                    river = shapely.geometry.LineString(coords)
                    node.segment = river
                    to_add.append((seg_handle, component, -1, node))
                    break

    # find the list of points to add to a give segment
    to_add_dict = dict()
    for seg_handle, component, endpoint, node in to_add:
        if seg_handle not in to_add_dict.keys():
            to_add_dict[seg_handle] = list()
        to_add_dict[seg_handle].append((component, endpoint, node))

    # find the set of points to add to each given segment
    def equal(p1,p2):
        if workflow.utils.close(p1[2].segment.coords[p1[1]], p2[2].segment.coords[p2[1]], 1.e-5):
            assert(p1[0] == p2[0])
            return True
        else:
            return False
    to_add_dict2 = dict()
    for seg_handle, insert_list in to_add_dict.items():
        new_list = []
        for p1 in insert_list:
            if (all(not equal(p1, p2) for p2 in new_list)):
                new_list.append(p1)
        to_add_dict2[seg_handle] = new_list

    # add these points to the segment
    for seg_handle, insert_list in to_add_dict2.items():
        seg = hucs.segments[seg_handle]
        # make a list of the coords and a flag to indicate a new
        # coord, then sort it by arclength along the segment.
        #
        # Note this needs special care if the seg is a loop, or else the endpoint gets sorted twice        
        if not workflow.utils.close(seg.coords[0], seg.coords[-1]):
            new_coords = sorted(
                [[c,0] for c in seg.coords]+[[p[2].segment.coords[p[1]],1] for p in insert_list],
                key = lambda a:seg.project(shapely.geometry.Point(a)))

            # determine the new coordinate indices
            breakpoint_inds = [i for i,(c,f) in enumerate(new_coords) if f is 1]

        else:
            new_coords = sorted(
                [[c,0] for c in seg.coords[:-1]]+[[p[2].segment.coords[p[1]],1] for p in insert_list],
                key = lambda a:seg.project(shapely.geometry.Point(a)))
            breakpoint_inds = [i for i,(c,f) in enumerate(new_coords) if f is 1]
            assert(len(breakpoint_inds) > 0)
            new_coords = new_coords[breakpoint_inds[0]:] + new_coords[0:breakpoint_inds[0]+1]
            new_coords[0][1] = 0
            new_coords[-1][1] = 0
            breakpoint_inds = [i for i,(c,f) in enumerate(new_coords) if f is 1]

        # now break into new segments
        new_segs = []
        ind_start = 0
        for ind_end in breakpoint_inds:
            assert(ind_end is not 0)
            new_segs.append(shapely.geometry.LineString([c for (c,f) in new_coords[ind_start:ind_end+1]]))
            ind_start = ind_end

        assert(ind_start < len(new_coords)-1)
        new_segs.append(shapely.geometry.LineString([tuple(c) for (c,f) in new_coords[ind_start:]]))

        # put all new_segs into the huc list.  Note insert_list[0][0] is the component
        hucs.segments[seg_handle] = new_segs.pop(0)
        new_handles = hucs.segments.add_many(new_segs)
        insert_list[0][0].add_many(new_handles)

    return river

def make_global_tree(rivers, tol=0.1):
    if len(rivers) is 0:
        return list()

    # make a kdtree of beginpoints
    coords = np.array([r.coords[0] for r in rivers])
    kdtree = scipy.spatial.cKDTree(coords)

    # make a node for each segment
    nodes = [workflow.tree.Tree(r) for r in rivers]

    # match nodes to their parent through the kdtree
    trees = []
    doublesegs = []
    doublesegs_matches = []
    doublesegs_winner = []
    for j,n in enumerate(nodes):
        # find the closest beginpoint the this node's endpoint
        closest = kdtree.query_ball_point(n.segment.coords[-1], tol)
        if len(closest) > 1:
            logging.debug("Bad multi segment:")
            logging.debug(" connected to %d: %r"%(j,list(n.segment.coords[-1])))
            doublesegs.append(j)
            doublesegs_matches.append(closest)

            # end at the same point, pick the min angle deviation
            my_tan = np.array(n.segment.coords[-1]) - np.array(n.segment.coords[-2])
            my_tan = my_tan / np.linalg.norm(my_tan)
            
            other_tans = [np.array(rivers[c].coords[1]) - np.array(rivers[c].coords[0]) for c in closest]
            other_tans = [ot/np.linalg.norm(ot) for ot in other_tans]
            dots = [np.inner(ot,my_tan) for ot in other_tans]
            for i,c in enumerate(closest):
                logging.debug("  %d: %r --> %r with dot product = %g"%(c,coords[c],rivers[c].coords[-1], dots[i]))
            c = closest[np.argmax(dots)]
            doublesegs_winner.append(c)
            nodes[c].addChild(n)

        elif len(closest) is 0:
            trees.append(n)
        else:
            nodes[closest[0]].addChild(n)
    return trees


def filter_rivers_to_huc(hucs, rivers, tol):
    """Filters out rivers not inside the HUCs provided."""
    # removes any rivers that are not at least partial contained in the hucs
    if type(rivers) is list and len(rivers) is 0:
        return list()

    logging.info("  ...forming union")
    union = shapely.ops.cascaded_union(list(hucs.polygons()))
    union = union.buffer(tol, 4)
    
    logging.info("  ...filtering")
    if type(rivers) is shapely.geometry.MultiLineString or \
       (type(rivers) is list and type(rivers[0]) is shapely.geometry.LineString):
        rivers2 = [r for r in rivers if union.intersects(r)]
    elif type(rivers) is list and type(rivers[0]) is workflow.tree.Tree:
        rivers2 = [r for river in rivers for r in river.dfs() if union.intersects(r)]

    logging.info("  ...making global tree")
    rivers_tree = workflow.hydrography.make_global_tree(rivers2, tol=0.1)
    logging.info("  ...done")
    return rivers_tree

def quick_cleanup(rivers, tol=0.1):
    """First pass to clean up hydro data"""
    logging.info("  quick cleaning rivers")
    assert(type(rivers) is shapely.geometry.MultiLineString)
    rivers = shapely.ops.linemerge(rivers).simplify(tol)
    return rivers

def cleanup(rivers, simp_tol=0.1, prune_tol=10, merge_tol=10):
    """Some hydrography data seems to get some random branches, typically
    quite short, that are nearly perfectly parallel to other, longer
    branches.  Surely this is a data error -- remove them.

    This returns rivers in a forest, not in a list.
    """
    # simplify
    if simp_tol is not None:
        for tree in rivers:
            simplify(tree, simp_tol)

    # prune short leaf branches and merge short interior reaches
    for tree in rivers:
        if merge_tol is not None:
            merge(tree, merge_tol)
        if merge_tol != prune_tol and prune_tol is not None:
            prune(tree, prune_tol)

def prune(tree, prune_tol=10):
    """Removes any leaf segments that are shorter than prune_tol"""
    for leaf in tree.leaf_nodes():
        if leaf.segment.length < prune_tol:
            logging.info("    cleaned leaf segment of length: %g at centroid %r"%(leaf.segment.length, leaf.segment.centroid.coords[0]))
            leaf.remove()

def merge(tree, tol=0.1):
    """Remove inner branches that are short, combining branchpoints as needed."""
    for node in list(tree.preOrder()):
        if node.segment.length < tol:
            logging.info("    cleaned inner segment of length %g at centroid %r"%(node.segment.length, node.segment.centroid.coords[0]))
            for child in node.children:
                child.segment = shapely.geometry.LineString(child.segment.coords[:-1]+[node.parent.segment.coords[0],])
                node.parent.addChild(child)
            node.remove()
            
def simplify(tree, tol=0.1):
    """Simplify, IN PLACE, all tree segments."""
    for node in tree.preOrder():
        if node.segment is not None:
            node.segment = node.segment.simplify(tol)
            