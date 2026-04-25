from vidsearch.ingest.images import ingest_image
from vidsearch.storage import pg as pg_store
from vidsearch.ingest.scanner import scan_corpus
from qdrant_client import QdrantClient

# Skip OCR by patching to be instant
import vidsearch.ingest.images as images_mod
_orig_run_ocr = images_mod.run_ocr

def fast_ocr(path):
    return []  # Skip OCR entirely

images_mod.run_ocr = fast_ocr

scan = scan_corpus("K:/projects/video_searcher/data/meme")

for fpath in scan.supported[:3]:
    result = ingest_image(fpath, force=True)
    print(f"{fpath.name}: {result.get('status', 'error')}")

# Check counts
with pg_store.get_cursor() as cur:
    cur.execute("SELECT COUNT(*) FROM core.images")
    pg_count = cur.fetchone()[0]

qc = QdrantClient(url="http://localhost:6333")
qdrant_count = qc.get_collection("memes").points_count

print(f"\nPG: {pg_count}, Qdrant: {qdrant_count}")
print("Match:", pg_count == qdrant_count)