#!/usr/bin/env python
"""The blob store abstraction."""

import abc
import time
from typing import Dict, Iterable, List, Optional

from grr_response_core.lib import rdfvalue
from grr_response_core.lib.util import precondition
from grr_response_core.stats import metrics
from grr_response_server.models import blobs as models_blobs

# Global blob stores registry.
#
# NOTE: this is a rudimentary registry that will be migrated to the uniform
# registry approach by hanuszczak@ (currently in the works).
REGISTRY = {}

BLOB_STORE_POLL_HIT_LATENCY = metrics.Event(
    "blob_store_poll_hit_latency",
    bins=[0.05, 0.1, 0.2, 0.5, 1, 2, 5, 10, 20, 50],
)
BLOB_STORE_POLL_HIT_ITERATION = metrics.Event(
    "blob_store_poll_hit_iteration", bins=[1, 2, 5, 10, 20, 50]
)


class BlobStoreTimeoutError(Exception):
  """An exception class raised when certain blob store operation times out."""


class BlobStore(metaclass=abc.ABCMeta):
  """The blob store base class."""

  def WriteBlobsWithUnknownHashes(
      self,
      blobs_data: Iterable[bytes],
  ) -> List[models_blobs.BlobID]:
    """Writes the contents of the given blobs, using their hash as BlobID.

    Args:
      blobs_data: An iterable of bytes objects.

    Returns:
      A list of blob identifiers corresponding to each written blob.
    """
    blobs_ids = [models_blobs.BlobID.Of(d) for d in blobs_data]
    self.WriteBlobs(dict(zip(blobs_ids, blobs_data)))
    return blobs_ids

  def WriteBlobWithUnknownHash(
      self,
      blob_data: bytes,
  ) -> models_blobs.BlobID:
    """Writes the content of the given blob, using its hash as BlobID.

    Args:
      blob_data: Blob contents as bytes.

    Returns:
      A blob identifier corresponding to the written blob.
    """
    return self.WriteBlobsWithUnknownHashes([blob_data])[0]

  def ReadBlob(
      self,
      blob_id: models_blobs.BlobID,
  ) -> Optional[bytes]:
    """Reads the blob contents, identified by the given BlobID.

    Args:
      blob_id: An identifier of the blob to read.

    Returns:
      Bytes corresponding to a given blob or None if such blob
      does not exist.
    """
    return self.ReadBlobs([blob_id])[blob_id]

  def CheckBlobExists(
      self,
      blob_id: models_blobs.BlobID,
  ) -> bool:
    """Checks if a blob with a given BlobID exists.

    Args:
      blob_id: An identifier of the blob to check for existence.

    Returns:
      True if the blob exists, False otherwise.
    """
    return self.CheckBlobsExist([blob_id])[blob_id]

  @abc.abstractmethod
  def WriteBlobs(
      self,
      blob_id_data_map: Dict[models_blobs.BlobID, bytes],
  ) -> None:
    """Creates or overwrites blobs.

    Args:
      blob_id_data_map: A mapping from blob identifiers to blob data to write.
    """

  @abc.abstractmethod
  def ReadBlobs(
      self, blob_ids: Iterable[models_blobs.BlobID]
  ) -> Dict[models_blobs.BlobID, Optional[bytes]]:
    """Reads all blobs, specified by blob_ids, returning their contents.

    Args:
      blob_ids: An iterable of BlobIDs.

    Returns:
      A map of {blob_id: blob_data} where blob_data is blob bytes previously
      written with WriteBlobs. If a particular blob_id is not found, the
      corresponding blob_data will be None.
    """

  @abc.abstractmethod
  def CheckBlobsExist(
      self,
      blob_ids: Iterable[models_blobs.BlobID],
  ) -> Dict[models_blobs.BlobID, bool]:
    """Checks if blobs for the given identifiers already exist.

    Args:
      blob_ids: An iterable of BlobIDs.

    Returns:
      A map of {blob_id: status} where status is a boolean (True if blob exists,
      False if it doesn't).
    """

  def ReadAndWaitForBlobs(
      self,
      blob_ids: Iterable[models_blobs.BlobID],
      timeout: rdfvalue.Duration,
  ) -> Dict[models_blobs.BlobID, Optional[bytes]]:
    """Reads specified blobs, waiting and retrying if blobs do not exist yet.

    Args:
      blob_ids: An iterable of BlobIDs.
      timeout: A rdfvalue.Duration specifying the maximum time to pass until the
        last poll is conducted. The overall runtime of ReadAndWaitForBlobs can
        be higher, because `timeout` is a threshold for the start (and not end)
        of the last attempt at reading.

    Returns:
      A map of {blob_id: blob_data} where blob_data is blob bytes previously
      written with WriteBlobs. If a particular blob_id is not found, the
      corresponding blob_data will be None.
    """
    remaining_ids = set(blob_ids)
    results = {blob_id: None for blob_id in remaining_ids}
    start = rdfvalue.RDFDatetime.Now()
    # TODO: Implement truncated exponential backoff.
    sleep_dur = rdfvalue.Duration.From(1, rdfvalue.SECONDS)
    poll_num = 0

    while remaining_ids:
      cur_blobs = self.ReadBlobs(list(remaining_ids))
      now = rdfvalue.RDFDatetime.Now()
      elapsed = now - start
      poll_num += 1

      for blob_id, blob in cur_blobs.items():
        if blob is None:
          continue
        results[blob_id] = blob
        remaining_ids.remove(blob_id)
        BLOB_STORE_POLL_HIT_LATENCY.RecordEvent(
            elapsed.ToFractional(rdfvalue.SECONDS)
        )
        BLOB_STORE_POLL_HIT_ITERATION.RecordEvent(poll_num)

      if not remaining_ids or elapsed + sleep_dur >= timeout:
        break

      time.sleep(sleep_dur.ToFractional(rdfvalue.SECONDS))

    return results

  def ReadAndWaitForBlob(
      self,
      blob_id: models_blobs.BlobID,
      timeout: rdfvalue.Duration,
  ) -> Optional[bytes]:
    """Reads the specified blobs waiting until it is available or times out.

    Args:
      blob_id: An identifier of the blob to read.
      timeout: A timeout after which `None` is returned instead.

    Returns:
      Content of the requested blob or `None` if the timeout was reached.
    """
    return self.ReadAndWaitForBlobs([blob_id], timeout)[blob_id]

  def WaitForBlobs(
      self,
      blob_ids: Iterable[models_blobs.BlobID],
      timeout: rdfvalue.Duration,
  ) -> None:
    """Waits for specified blobs to appear in the database.

    Args:
      blob_ids: A collection of blob ids to await for.
      timeout: A duration specifying the maximum amount of time to wait.

    Raises:
      BlobStoreTimeoutError: If the blobs are still not in the database after
        the specified timeout duration has elapsed.
    """
    remaining_blob_ids = set(blob_ids)

    # TODO: See a TODO comment in `RunAndWaitForBlobs`.
    sleep_duration = rdfvalue.Duration.From(1, rdfvalue.SECONDS)

    start_time = rdfvalue.RDFDatetime.Now()
    ticks = 0

    while True:
      blob_id_exists = self.CheckBlobsExist(remaining_blob_ids)

      elapsed = rdfvalue.RDFDatetime.Now() - start_time
      elapsed_secs = elapsed.ToFractional(rdfvalue.SECONDS)
      ticks += 1

      for blob_id, exists in blob_id_exists.items():
        if not exists:
          continue

        remaining_blob_ids.remove(blob_id)

        BLOB_STORE_POLL_HIT_LATENCY.RecordEvent(elapsed_secs)
        BLOB_STORE_POLL_HIT_ITERATION.RecordEvent(ticks)

      if not remaining_blob_ids:
        break

      if elapsed + sleep_duration >= timeout:
        raise BlobStoreTimeoutError()

      sleep_duration_secs = sleep_duration.ToFractional(rdfvalue.SECONDS)
      time.sleep(sleep_duration_secs)


class BlobStoreValidationWrapper(BlobStore):
  """BlobStore wrapper that validates calls arguments."""

  def __init__(self, delegate: BlobStore):
    super().__init__()
    self.delegate = delegate

  def WriteBlobsWithUnknownHashes(
      self,
      blobs_data: Iterable[bytes],
  ) -> List[models_blobs.BlobID]:
    precondition.AssertIterableType(blobs_data, bytes)
    return self.delegate.WriteBlobsWithUnknownHashes(blobs_data)

  def WriteBlobWithUnknownHash(
      self,
      blob_data: bytes,
  ) -> models_blobs.BlobID:
    precondition.AssertType(blob_data, bytes)
    return self.delegate.WriteBlobWithUnknownHash(blob_data)

  def ReadBlob(
      self,
      blob_id: models_blobs.BlobID,
  ) -> Optional[bytes]:
    precondition.AssertType(blob_id, models_blobs.BlobID)
    return self.delegate.ReadBlob(blob_id)

  def CheckBlobExists(
      self,
      blob_id: models_blobs.BlobID,
  ) -> bool:
    precondition.AssertType(blob_id, models_blobs.BlobID)
    return self.delegate.CheckBlobExists(blob_id)

  def WriteBlobs(
      self,
      blob_id_data_map: Dict[models_blobs.BlobID, bytes],
  ) -> None:
    precondition.AssertDictType(blob_id_data_map, models_blobs.BlobID, bytes)
    return self.delegate.WriteBlobs(blob_id_data_map)

  def ReadBlobs(
      self, blob_ids: Iterable[models_blobs.BlobID]
  ) -> Dict[models_blobs.BlobID, Optional[bytes]]:
    precondition.AssertIterableType(blob_ids, models_blobs.BlobID)
    return self.delegate.ReadBlobs(blob_ids)

  def CheckBlobsExist(
      self,
      blob_ids: Iterable[models_blobs.BlobID],
  ) -> Dict[models_blobs.BlobID, bool]:
    precondition.AssertIterableType(blob_ids, models_blobs.BlobID)
    return self.delegate.CheckBlobsExist(blob_ids)
