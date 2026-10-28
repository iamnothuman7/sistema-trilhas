"""Compensate new image writes if saving the raffle or its audit fails."""
from contextlib import contextmanager


@contextmanager
def raffle_image_writes(raffle):
    previous={name:getattr(raffle,name).name for name in ('primary_image','secondary_image')}
    try:
        yield
    except Exception:
        for name,old in previous.items():
            asset=getattr(raffle,name)
            if asset and asset._committed and asset.name!=old:
                asset.storage.delete(asset.name)
        raise
