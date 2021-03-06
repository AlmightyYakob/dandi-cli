"""Classes/utilities for support of a dandiset"""

import os
from pathlib import Path

from .consts import dandiset_metadata_file
from .utils import find_parent_directory_containing, yaml_dump, yaml_load

from . import get_logger

lgr = get_logger()


class Dandiset(object):
    """A prototype class for all things dandiset
    """

    __slots__ = ["metadata", "path", "path_obj", "_metadata_file_obj"]

    def __init__(self, path, allow_empty=False):
        self.path = str(path)
        self.path_obj = Path(path)
        if not allow_empty and not (self.path_obj / dandiset_metadata_file).exists():
            raise ValueError(f"No dandiset at {path}")

        self.metadata = None
        self._metadata_file_obj = self.path_obj / dandiset_metadata_file
        self._load_metadata()

    @classmethod
    def find(cls, path):
        """Find a dandiset possibly pointing to a directory within it
        """
        dandiset_path = find_parent_directory_containing(dandiset_metadata_file, path)
        if dandiset_path:
            return cls(dandiset_path)
        return None

    def _load_metadata(self):
        if self._metadata_file_obj.exists():
            with open(self._metadata_file_obj) as f:
                # TODO it would cast 000001 if not explicitly string into
                # an int -- we should prevent it... probably with some custom loader
                self.metadata = yaml_load(f, typ="safe")
        else:
            self.metadata = None

    @classmethod
    def get_dandiset_record(cls, meta):
        dandiset_identifier = cls._get_identifier(meta)
        if not dandiset_identifier:
            lgr.warning("No identifier for a dandiset was provided in %s", str(meta))
            obtain_msg = ""
        else:
            obtain_msg = " edited online at https://dandiarchive.org/dandiset/{}{}# and".format(
                dandiset_identifier, os.linesep
            )
        header = f"""\
# DO NOT EDIT THIS FILE LOCALLY. ALL LOCAL UPDATES WILL BE LOST.
# It can be{obtain_msg} obtained from the dandiarchive.
"""
        yaml_rec = yaml_dump(meta)
        return header + yaml_rec

    def update_metadata(self, meta):
        """Update existing metadata record in dandiset.yaml
        """
        if not meta:
            lgr.debug("No updates to metadata, returning")
            return

        if self._metadata_file_obj.exists():
            with open(self._metadata_file_obj) as f:
                rec = yaml_load(f, typ="safe")
        else:
            rec = {}

        # TODO: decide howto and properly do updates to nested structures if
        # possible.  Otherwise limit to the fields we know could be modified
        # locally
        rec.update(meta)

        self._metadata_file_obj.write_text(self.get_dandiset_record(rec))

        # and reload now by a pure yaml
        self._load_metadata()

    @classmethod
    def _get_identifier(cls, metadata):
        """Given a metadata record, determine identifier"""
        # ATM since we have dichotomy in dandiset metadata schema from drafts
        # and from published versions, we will just test both locations
        id_ = metadata.get("dandiset", {}).get("identifier")
        if id_:
            lgr.debug("Found identifier %s in 'dandiset.identifier'", id_)

        if not id_ and "identifier" in metadata:
            id_ = metadata["identifier"]
            lgr.debug("Found identifier %s in top level 'identifier'", id_)

        return id_

    @property
    def identifier(self):
        id_ = self._get_identifier(self.metadata)
        if not id_:
            raise ValueError(
                f"Found no dandiset.identifier in metadata record: {self.metadata}"
            )
        return id_
