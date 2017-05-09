import os
from datetime import datetime, timedelta
from functools import singledispatch
from typing import Iterable, Callable

import structlog

from datacube.utils import uri_to_local_path
from datacubenci import paths
from .differences import DatasetNotIndexed, Mismatch, ArchivedDatasetOnDisk, LocationNotIndexed, LocationMissingOnDisk
from .index import DatasetPathIndex

_LOG = structlog.get_logger()


# underscore function names are the norm with singledispatch
# pylint: disable=function-redefined

@singledispatch
def do_index_missing(mismatch: Mismatch, index: DatasetPathIndex):
    pass


@do_index_missing.register(DatasetNotIndexed)
def _(mismatch: DatasetNotIndexed, index: DatasetPathIndex):
    index.add_dataset(mismatch.dataset, mismatch.uri)


@singledispatch
def do_update_locations(mismatch: Mismatch, index: DatasetPathIndex):
    pass


@do_update_locations.register(LocationMissingOnDisk)
def _(mismatch: LocationMissingOnDisk, index: DatasetPathIndex):
    index.remove_location(mismatch.dataset, mismatch.uri)


@do_update_locations.register(LocationNotIndexed)
def _(mismatch: LocationNotIndexed, index: DatasetPathIndex):
    index.add_location(mismatch.dataset, mismatch.uri)


@singledispatch
def do_trash_archived(mismatch: Mismatch, index: DatasetPathIndex, min_age_hours: int):
    pass


@do_trash_archived.register(ArchivedDatasetOnDisk)
def _(mismatch: ArchivedDatasetOnDisk, index: DatasetPathIndex, min_age_hours: int):

    # Must have been archived more than min_age_hours ago to trash.
    if mismatch.dataset.archived_time > (datetime.utcnow() - timedelta(hours=min_age_hours)):
        _LOG.info("do_trash_archived.too_young", dataset_id=mismatch.dataset.id)
        return

    local_path = uri_to_local_path(mismatch.uri)
    if not local_path.exists():
        _LOG.warning("do_trash_archived.not_exist", path=local_path)
        return

    _trash(local_path)


@singledispatch
def do_trash_missing(mismatch: Mismatch, index: DatasetPathIndex):
    pass


@do_trash_missing.register(DatasetNotIndexed)
def _(mismatch: DatasetNotIndexed, index: DatasetPathIndex):
    local_path = uri_to_local_path(mismatch.uri)

    if not local_path.exists():
        _LOG.warning("do_trash_missing.not_exist", path=local_path)
        return

    _trash(local_path)


def _trash(local_path):
    # TODO: to handle sibling-metadata we should trash "all_dataset_paths" too.
    base_path, all_dataset_files = paths.get_dataset_paths(local_path)

    trash_path = paths.get_trash_path(base_path)

    _LOG.info("trashing", base_path=base_path, trash_path=trash_path)
    if not trash_path.parent.exists():
        os.makedirs(str(trash_path.parent))
    os.rename(str(base_path), str(trash_path))


def fix_mismatches(mismatches: Iterable[Mismatch],
                   index: DatasetPathIndex,
                   index_missing=False,
                   trash_missing=False,
                   trash_archived=False,
                   min_trash_age_hours=72,
                   update_locations=False,
                   pre_fix: Callable[[Mismatch], None]=None):
    if index_missing and trash_missing:
        raise RuntimeError("Datasets missing from the index can either be indexed or trashed, but not both.")

    for mismatch in mismatches:
        _LOG.info('mismatch.found', mismatch=mismatch)

        if pre_fix:
            pre_fix(mismatch)

        if update_locations:
            do_update_locations(mismatch, index)

        if index_missing:
            do_index_missing(mismatch, index)
        elif trash_missing:
            do_trash_missing(mismatch)

        if trash_archived:
            _LOG.info('mismatch.trash', mismatch=mismatch)
            do_trash_archived(mismatch, index, min_age_hours=min_trash_age_hours)
