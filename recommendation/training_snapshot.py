"""Order-independent content identity for a normalized training event frame."""

from hashlib import sha256

import pyarrow as pa
import pyarrow.compute as pc


def training_snapshot_sha256(events):
    """Hash typed row values and duplicates, independent of display order and index."""
    if not events.columns.is_unique:
        raise ValueError("Training snapshot columns must be unique")
    columns = sorted(events.columns)
    table = pa.Table.from_pandas(events[columns], preserve_index=False).replace_schema_metadata(None)
    for index, field in enumerate(table.schema):
        if pa.types.is_dictionary(field.type):
            table = table.set_column(index, field.name, pc.dictionary_decode(table.column(index)))
        elif pa.types.is_floating(field.type):
            column = table.column(index)
            zero = pa.scalar(0, type=field.type)
            # Signed zeros compare equal when sorting, so give them identical bytes.
            table = table.set_column(index, field, pc.if_else(pc.equal(column, zero), zero, column))
    canonical = table.sort_by([(name, "ascending") for name in columns]).combine_chunks()
    digest = sha256(b"training-snapshot:arrow-ipc:v1\0")
    digest.update(canonical.schema.serialize())
    for batch in canonical.to_batches(max_chunksize=65_536):
        digest.update(batch.serialize())
    return digest.hexdigest()
