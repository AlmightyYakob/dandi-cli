from datetime import datetime
import os.path as op
import re
from uuid import uuid4
from . import models
from .pynwb_utils import (
    _get_pynwb_metadata,
    get_neurodata_types,
    get_nwb_version,
    ignore_benign_pynwb_warnings,
    metadata_cache,
)
from .utils import ensure_datetime

from . import __version__, get_logger
from .dandiset import Dandiset

lgr = get_logger()


@metadata_cache.memoize_path
def get_metadata(path):
    """Get selected metadata from a .nwb file or a dandiset directory

    If a directory given and it is not a Dandiset, None is returned

    Parameters
    ----------
    path: str or Path

    Returns
    -------
    dict
    """
    # when we run in parallel, these annoying warnings appear
    ignore_benign_pynwb_warnings()
    path = str(path)  # for Path
    meta = dict()

    if op.isdir(path):
        try:
            dandiset = Dandiset(path)
            return dandiset.metadata
        except ValueError as exc:
            lgr.debug("Failed to get metadata for %s: %s", path, exc)
            return None

    # First read out possibly available versions of specifications for NWB(:N)
    meta["nwb_version"] = get_nwb_version(path)

    # PyNWB might fail to load because of missing extensions.
    # There is a new initiative of establishing registry of such extensions.
    # Not yet sure if PyNWB is going to provide "native" support for needed
    # functionality: https://github.com/NeurodataWithoutBorders/pynwb/issues/1143
    # So meanwhile, hard-coded workaround for data types we care about
    ndtypes_registry = {
        "AIBS_ecephys": "allensdk.brain_observatory.ecephys.nwb",
        "ndx-labmetadata-abf": "ndx_dandi_icephys",
    }
    tried_imports = set()
    while True:
        try:
            meta.update(_get_pynwb_metadata(path))
            break
        except KeyError as exc:  # ATM there is
            lgr.debug("Failed to read %s: %s", path, exc)
            import re

            res = re.match(r"^['\"\\]+(\S+). not a namespace", str(exc))
            if not res:
                raise
            ndtype = res.groups()[0]
            if ndtype not in ndtypes_registry:
                raise ValueError(
                    "We do not know which extension provides %s. "
                    "Original exception was: %s. " % (ndtype, exc)
                )
            import_mod = ndtypes_registry[ndtype]
            lgr.debug("Importing %r which should provide %r", import_mod, ndtype)
            if import_mod in tried_imports:
                raise RuntimeError(
                    "We already tried importing %s to provide %s, but it seems it didn't help"
                    % (import_mod, ndtype)
                )
            tried_imports.add(import_mod)
            __import__(import_mod)

    meta["nd_types"] = get_neurodata_types(path)

    return meta


def parse_age(age):
    """
    Convert a human-friendly duration string into an ISO 8601 duration

    Parameters
    ----------
    age : str

    Returns
    -------
    str
    """
    m = re.fullmatch(r"(\d+)\s*(y(ear)?|m(onth)?|w(eek)?|d(ay)?)s?", age, flags=re.I)
    if m:
        qty = int(m.group(1))
        unit = m.group(2)[0].upper()
        return f"P{qty}{unit}"
    else:
        raise ValueError(age)


def extract_age(metadata):
    try:
        dob = ensure_datetime(metadata["date_of_birth"])
        start = ensure_datetime(metadata["session_start_time"])
    except (KeyError, TypeError, ValueError):
        try:
            duration = parse_age(metadata["age"])
        except (KeyError, TypeError, ValueError):
            return ...
    else:
        if start < dob:
            raise ValueError("session_start_time precedes date_of_birth")
        duration = timedelta2duration(start - dob)
    return models.PropertyValue(value=duration, unitText="Years from birth")


def timedelta2duration(delta):
    """
    Convert a datetime.timedelta to ISO 8601 duration format

    Parameters
    ----------
    delta : datetime.timedelta

    Returns
    -------
    str
    """
    s = "P"
    if delta.days:
        s += f"{delta.days}D"
    if delta.seconds or delta.microseconds:
        sec = delta.seconds
        if delta.microseconds:
            # Don't add when microseconds is 0, so that sec will be an int then
            sec += delta.microseconds / 1000000
        s += f"T{sec}S"
    if s == "P":
        s += "0D"
    return s


def extract_sex(metadata):
    if metadata.get("sex") is not None:
        return models.SexType(identifier="sex", name=metadata["sex"])
    else:
        return ...


def extract_assay_type(metadata):
    if "assayType" in metadata:
        return [models.AssayType(identifier="assayType", name=metadata["assayType"])]
    else:
        return []


def extract_anatomy(metadata):
    if "anatomy" in metadata:
        return [models.Anatomy(identifier="anatomy", name=metadata["anatomy"])]
    else:
        return []


def extract_model(modelcls, metadata, **kwargs):
    m = modelcls.unvalidated()
    for field in m.__fields__.keys():
        value = kwargs.get(field, extract_field(field, metadata))
        if value is not Ellipsis:
            setattr(m, field, value)
    # return modelcls(**m.dict())
    return m


def extract_wasDerivedFrom(metadata):
    return [
        extract_model(models.BioSample, metadata, identifier=metadata.get("subject_id"))
    ]


def extract_digest(metadata):
    if "digest" in metadata:
        return models.Digest(
            value=metadata["digest"],
            cryptoType=models.DigestType[metadata["digest_type"]],
        )
    else:
        return ...


FIELD_EXTRACTORS = {
    "wasDerivedFrom": extract_wasDerivedFrom,
    "age": extract_age,
    "sex": extract_sex,
    "assayType": extract_assay_type,
    "anatomy": extract_anatomy,
    "digest": extract_digest,
}


def extract_field(field, metadata):
    if field in FIELD_EXTRACTORS:
        return FIELD_EXTRACTORS[field](metadata)
    else:
        return metadata.get(field, ...)


def nwb2asset(nwb_path, digest=None, digest_type=None):
    start_time = datetime.now().astimezone()
    metadata = get_metadata(nwb_path)
    if digest is not None:
        metadata["digest"] = digest
        metadata["digest_type"] = digest_type
    metadata["contentSize"] = op.getsize(nwb_path)
    metadata["encodingFormat"] = "application/x-nwb"
    asset = metadata2asset(metadata)
    end_time = datetime.now().astimezone()
    asset.wasGeneratedBy = models.Activity(
        identifier=str(uuid4()),
        name="NWB conversion",
        description="Data converted using DANDI cli",
        wasAssociatedWith=models.Software(
            identifier={"propertyID": "RRID", "value": "SCR_019009"},
            name="DANDI Command Line Interface",
            description=f"dandi-cli {__version__}",
            version=__version__,
            url="https://github.com/dandi/dandi-cli",
        ),
        startedAt=start_time,
        endedAt=end_time,
    )
    return asset


def metadata2asset(metadata):
    return extract_model(models.AssetMeta, metadata)
