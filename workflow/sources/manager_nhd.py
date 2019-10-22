"""Manager for interacting with USGS National Hydrography Datasets.
"""
import os, sys
import logging
import fiona
import shapely
import attr

import workflow.sources.utils as source_utils
import workflow.conf
import workflow.sources.names
import workflow.utils
import workflow.warp


@attr.s
class _FileManagerNHD:
    name = attr.ib(type=str)
    file_level = attr.ib(type=int)
    lowest_level = attr.ib(type=int)
    name_manager = attr.ib()

    def get_huc(self, huc):
        huc = source_utils.huc_str(huc)
        profile, hus = self.get_hucs(huc, len(huc))
        assert(len(hus) == 1)
        return profile, hus[0]

    def get_hucs(self, huc, level):
        """Loads HUCs from file."""
        huc = source_utils.huc_str(huc)
        huc_level = len(huc)

        # error checking on the levels, require file_level <= huc_level <= level <= lowest_level
        if self.lowest_level < level:
            raise ValueError("{}: files include HUs at max level {}.".format(self.name, self.lowest_level))
        if level < huc_level:
            raise ValueError("{}: cannot ask for HUs at level {} contained in {}.".format(self.name, level, huc_level))
        if huc_level < self.file_level:
            raise ValueError("{}: files are organized at HUC level {}, so cannot ask for a larger HUC than that level.".format(self.name, self.file_level))

        # download the file
        filename = self._download(huc[0:self.file_level])
        logging.info('Using HUC file "{}"'.format(filename))
        

        # read the file
        layer = 'WBDHU{}'.format(level)
        logging.debug("{}: opening '{}' layer '{}' for HUCs in '{}'".format(self.name, filename, layer, huc))
        with fiona.open(filename, mode='r', layer=layer) as fid:
            hus = [hu for hu in fid if hu['properties']['HUC{:d}'.format(level)].startswith(huc)]
            profile = fid.profile
        return profile, hus
        
    def get_hydro(self, huc, bounds=None, bounds_crs=None):
        """Downloads and reads hydrography within these bounds and/or huc.

        Note this requires a HUC hint of at least a level 4 HUC which contains bounds.
        """
        if 'WBD' in self.name:
            raise RuntimeError('{}: does not provide hydrographic data.'.format(self.name))
        
        huc = source_utils.huc_str(huc)
        hint_level = len(huc)

        # try to get bounds if not provided
        if bounds is None:
            # can we infer a bounds by getting the HUC?
            profile, hu = self.get_huc(huc)
            bounds = workflow.utils.bounds(hu)
            bounds_crs = profile['crs']
        
        # error checking on the levels, require file_level <= huc_level <= lowest_level
        if hint_level < self.file_level:
            raise ValueError("{}: files are organized at HUC level {}, so cannot ask for a larger HUC than that level.".format(self.name, self.file_level))
        
        # download the file
        filename = self._download(huc[0:self.file_level])
        logging.info('Using Hydrography file "{}"'.format(filename))
        
        # find and open the hydrography layer        
        filename = self.name_manager.file_name(huc[0:self.file_level])
        layer = 'NHDFlowline'
        logging.debug("{}: opening '{}' layer '{}' for streams in '{}'".format(self.name, filename, layer, bounds))
        with fiona.open(filename, mode='r', layer=layer) as fid:
            profile = fid.profile
            bounds = workflow.warp.warp_bounds(bounds, bounds_crs, profile['crs'])
            rivers = [r for (i,r) in fid.items(bbox=bounds)]
        return profile, rivers
            
    def _url(self, hucstr):
        """Use the REST API to find the URL."""
        import requests
        rest_url = 'https://viewer.nationalmap.gov/tnmaccess/api/products'
        hucstr = hucstr[0:self.file_level]

        def attempt(params):        
            r = requests.get(rest_url, params=params)
            try:
                r.raise_for_status()
            except Exception as e:
                logging.error(e)
                return 1,e
                
            json = r.json()

            # this feels hacky, but it does not appear that USGS has their
            # 'prodFormat' get option or 'format' return json value
            # working correctly.
            matches = [m for m in json['items']]

            # filter for GDBs
            matches = [m for m in matches if 'downloadURL' in m and 'GDB' in m['downloadURL']]

            # filter for title contains HUC string
            matches_f = [m for m in matches if hucstr in m['title'].split()]
            if len(matches_f) > 0:
                matches = matches_f
        
            if len(matches) == 0:
                return 1, '{}: not able to find HUC {}'.format(self.name, hucstr)
            if len(matches) > 1:
                logging.error('{}: too many matches for HUC {} ({})'.format(self.name, hucstr, len(matches)))
                for m in matches:
                    logging.error(' {}\n   {}'.format(m['title'], m['downloadURL']))
                return 1, '{}: too many matches for HUC {}'.format(self.name, hucstr)
            return 0, matches[0]['downloadURL']

        # cheaper if it works, may not work in alaska?
        a1 = attempt({'datasets':self.name,
                      'polyType':'huc{}'.format(self.file_level),
                      'polyCode':hucstr})
        if not a1[0]:
            return a1[1]

        # works more univerasally but is a BIG lookup, then filter locally
        a2 = attempt({'datasets':self.name})
        if not a2[0]:
            return a2[1]

        raise ValueError('{}: cannot find HUC {}'.format(self.name, hucstr))
        

    def _download(self, hucstr, force=False):
        """Download the data."""
        # check directory structure
        os.makedirs(self.name_manager.data_dir(), exist_ok=True)
        os.makedirs(self.name_manager.folder_name(hucstr), exist_ok=True)

        work_folder = self.name_manager.raw_folder_name(hucstr)
        os.makedirs(work_folder, exist_ok=True)

        filename = self.name_manager.file_name(hucstr)
        if not os.path.exists(filename) or force:
            url = self._url(hucstr)

            downloadfile = os.path.join(work_folder, url.split("/")[-1])
            if not os.path.exists(downloadfile) or force:
                logging.debug("Attempting to download source for target '%s'"%filename)
                source_utils.download(url, downloadfile, force)
                
            source_utils.unzip(downloadfile, work_folder)

            # hope we can find it?
            gdb_files = [f for f in os.listdir(work_folder) if f.endswith('.gdb')]
            assert(len(gdb_files) == 1)
            source_utils.move(os.path.join(work_folder, gdb_files[0]), filename)

        if not os.path.exists(filename):
            raise RuntimeError("Cannot find or download file for source target '%s'"%filename)
        return filename
    
    
class FileManagerNHDPlus(_FileManagerNHD):
    def __init__(self):
        name = 'National Hydrography Dataset Plus High Resolution (NHDPlus HR)'
        super().__init__(name, 4, 12,
                         workflow.sources.names.Names(name, 'hydrography',
                                                      'NHDPlus_H_{}_GDB',
                                                      'NHDPlus_H_{}.gdb'))

class FileManagerNHD(_FileManagerNHD):
    def __init__(self):
        name = 'National Hydrography Dataset (NHD)'
        super().__init__(name, 8, 12,
                         workflow.sources.names.Names(name, 'hydrography',
                                                      'NHD_H_{}_GDB',
                                                      'NHD_H_{}.gdb'))


class FileManagerWBD(_FileManagerNHD):
    def __init__(self):
        name = 'National Watershed Boundary Dataset (WBD)'
        super().__init__(name, 2, 12,
                         workflow.sources.names.Names(name, 'hydrography',
                                                      'WBD_{}_GDB',
                                                      'WBD_{}.gdb'))    
    
