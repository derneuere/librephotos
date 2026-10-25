"""Seed a large synthetic library (issue #2868 scale) straight into Postgres.

Run with: manage.py shell -c "exec(open(r'<this file>').read())"
Env: SEED_PHOTOS (default 158000), SEED_EMBED_DIM (default 512).
"""

import os
import time

from django.db import connection

from api.models import User

N = int(os.environ.get("SEED_PHOTOS", "158000"))
DIM = int(os.environ.get("SEED_EMBED_DIM", "512"))
OPENCLIP = "openclip_vitb32"
TAG = f"{OPENCLIP}_tag"

t0 = time.time()


def step(name, sql, params=None):
    s = time.time()
    with connection.cursor() as c:
        c.execute(sql, params)
    print(f"{name}: {time.time() - s:.1f}s", flush=True)


user = User.objects.filter(username="big").first()
if user is None:
    user = User.objects.create_superuser("big", "big@example.com", "big-password-1")
small = User.objects.filter(username="small").first()
if small is None:
    small = User.objects.create_user("small", "small@example.com", "small-password-1")
uid = user.id

step(
    "files",
    """
    INSERT INTO api_file (hash, path, type, missing)
    SELECT md5(i::text) || %(u)s, '/data/photos/' || (i / 1000) || '/IMG_' || i || '.jpg', 1, false
    FROM generate_series(1, %(n)s) i
    """,
    {"n": N, "u": str(uid)},
)

step(
    "photos",
    """
    INSERT INTO api_photo (id, image_hash, added_on, exif_timestamp, "timestamp", hidden, public,
        owner_id, video, rating, in_trashcan, size, last_modified, removed, local_orientation,
        is_screenshot, is_document, category_source, archived, ocr_failures, town, main_file_id,
        geolocation_json, clip_embeddings_model, clip_embeddings_magnitude, clip_embeddings)
    SELECT
        md5('p' || i)::uuid,
        md5(i::text) || %(u)s,
        now() - (i || ' minutes')::interval,
        timestamp '2010-01-01' + (i * 2700 || ' seconds')::interval,
        timestamp '2010-01-01' + (i * 2700 || ' seconds')::interval,
        i %% 200 = 0, false, %(uid)s, i %% 15 = 0, i %% 6, i %% 500 = 0,
        2000000 + (i %% 5000000), now(), false, 1,
        i %% 40 = 0, i %% 60 = 0, 'auto', false, 0, '',
        md5(i::text) || %(u)s,
        CASE WHEN i %% 10 < 7 THEN jsonb_build_object('features', jsonb_build_array(
            jsonb_build_object('text', 'Town ' || (i %% 300), 'place_type', jsonb_build_array('place'), 'center', jsonb_build_array(10.0 + (i %% 300) * 0.01, 50.0)),
            jsonb_build_object('text', 'Region ' || (i %% 40), 'place_type', jsonb_build_array('region'), 'center', jsonb_build_array(10.0, 50.0)),
            jsonb_build_object('text', 'Country ' || (i %% 8), 'place_type', jsonb_build_array('country'), 'center', jsonb_build_array(10.0, 50.0))))
        END,
        %(model)s, 1.0,
        (SELECT jsonb_agg(round((random() - 0.5)::numeric, 6)) FROM generate_series(1, %(dim)s) d WHERE i > 0)
    FROM generate_series(1, %(n)s) i
    """,
    {"n": N, "u": str(uid), "uid": uid, "model": OPENCLIP, "dim": DIM},
)

step(
    "photo_files",
    """
    INSERT INTO api_photo_files (photo_id, file_id)
    SELECT md5('p' || i)::uuid, md5(i::text) || %(u)s FROM generate_series(1, %(n)s) i
    """,
    {"n": N, "u": str(uid)},
)

step(
    "thumbnails",
    """
    INSERT INTO api_thumbnail (photo_id, thumbnail_big, square_thumbnail, square_thumbnail_small,
        aspect_ratio, video_poster, dominant_color)
    SELECT md5('p' || i)::uuid, 'b', 's', 'ss', CASE WHEN i %% 250 = 0 THEN NULL ELSE 1.5 END,
        false, '#808080'
    FROM generate_series(1, %(n)s) i
    """,
    {"n": N},
)

step(
    "captions",
    """
    INSERT INTO api_photo_caption (photo_id, captions_json, created_at, updated_at)
    SELECT md5('p' || i)::uuid,
        jsonb_build_object(%(model)s, jsonb_build_object('tags',
            jsonb_build_array('tag' || (i %% 2000), 'tag' || ((i * 7) %% 2000), 'tag' || ((i * 13) %% 2000))))
        || CASE WHEN i %% 2 = 0 THEN jsonb_build_object('im2txt', 'a photo of thing ' || (i %% 97)) ELSE '{}'::jsonb END
        || CASE WHEN i %% 100 = 0 THEN jsonb_build_object('user_caption', 'my caption ' || i) ELSE '{}'::jsonb END,
        now(), now()
    FROM generate_series(1, %(n)s) i
    """,
    {"n": N, "model": OPENCLIP},
)

step(
    "search",
    """
    INSERT INTO api_photo_search (photo_id, search_folded, search_captions, search_location, created_at, updated_at)
    SELECT md5('p' || i)::uuid, 'tag' || (i %% 2000), 'tag' || (i %% 2000), 'town ' || (i %% 300), now(), now()
    FROM generate_series(1, %(n)s) i
    """,
    {"n": N},
)

# 2000 tag albums, every photo in 8 of them (~1.26M links).
step(
    "albumthing",
    """
    INSERT INTO api_albumthing (title, favorited, owner_id, photo_count, last_modified, thing_type)
    SELECT 'tag' || a, false, %(uid)s, 0, now(), %(tag)s FROM generate_series(0, 1999) a
    """,
    {"uid": uid, "tag": TAG},
)
step(
    "albumthing_photos",
    """
    INSERT INTO api_albumthing_photos (albumthing_id, photo_id)
    SELECT DISTINCT t.id, md5('p' || i)::uuid
    FROM generate_series(1, %(n)s) i
    CROSS JOIN generate_series(0, 7) k
    JOIN api_albumthing t ON t.owner_id = %(uid)s AND t.title = 'tag' || ((i * (k * 2 + 1) + k * 131) %% 2000)
    """,
    {"n": N, "uid": uid},
)
step(
    "albumthing counts",
    """
    UPDATE api_albumthing t SET photo_count = c.n
    FROM (SELECT albumthing_id, count(*) n FROM api_albumthing_photos GROUP BY 1) c
    WHERE c.albumthing_id = t.id
    """,
)

step(
    "albumplace",
    """
    INSERT INTO api_albumplace (title, favorited, owner_id, last_modified, geolocation_level)
    SELECT 'Town ' || a, false, %(uid)s, now(), 3 FROM generate_series(0, 299) a
    UNION ALL SELECT 'Region ' || a, false, %(uid)s, now(), 2 FROM generate_series(0, 39) a
    UNION ALL SELECT 'Country ' || a, false, %(uid)s, now(), 1 FROM generate_series(0, 7) a
    """,
    {"uid": uid},
)
step(
    "albumplace_photos",
    """
    INSERT INTO api_albumplace_photos (albumplace_id, photo_id)
    SELECT p.id, md5('p' || i)::uuid
    FROM generate_series(1, %(n)s) i
    JOIN api_albumplace p ON p.owner_id = %(uid)s AND p.title IN
        ('Town ' || (i %% 300), 'Region ' || (i %% 40), 'Country ' || (i %% 8))
    WHERE i %% 10 < 7
    """,
    {"n": N, "uid": uid},
)

step(
    "albumauto",
    """
    INSERT INTO api_albumauto (title, "timestamp", created_on, favorited, owner_id, last_modified)
    SELECT 'Event ' || a, now() - (a || ' hours')::interval, now(), false, %(uid)s, now() FROM generate_series(0, 3999) a
    """,
    {"uid": uid},
)
step(
    "albumauto_photos",
    """
    INSERT INTO api_albumauto_photos (albumauto_id, photo_id)
    SELECT (SELECT min(id) FROM api_albumauto) + (i / 40) %% 4000, md5('p' || i)::uuid
    FROM generate_series(1, %(n)s) i WHERE i %% 3 = 0
    """,
    {"n": N},
)

step(
    "albumdate",
    """
    INSERT INTO api_albumdate (title, date, favorited, owner_id)
    SELECT 'day ' || d, date '2010-01-01' + d, false, %(uid)s FROM generate_series(0, 4999) d
    """,
    {"uid": uid},
)
step(
    "albumdate_photos",
    """
    INSERT INTO api_albumdate_photos (albumdate_id, photo_id)
    SELECT (SELECT min(id) FROM api_albumdate) + (i * 2700 / 86400) %% 5000, md5('p' || i)::uuid
    FROM generate_series(1, %(n)s) i
    """,
    {"n": N},
)

step(
    "albumuser",
    """
    INSERT INTO api_albumuser (title, created_on, favorited, owner_id, last_modified, locked,
        members_share_location, members_share_faces, members_share_camera_info,
        members_share_captions, members_share_originals, comments_enabled)
    SELECT 'Album ' || a, now(), false, %(uid)s, now(), false, false, false, false, false, false, false
    FROM generate_series(0, 99) a
    """,
    {"uid": uid},
)
step(
    "albumuser_photos",
    """
    INSERT INTO api_albumuser_photos (albumuser_id, photo_id)
    SELECT (SELECT min(id) FROM api_albumuser) + (i / 7) %% 100, md5('p' || i)::uuid
    FROM generate_series(1, %(n)s) i WHERE i %% 7 = 0
    """,
    {"n": N},
)

step(
    "persons",
    """
    INSERT INTO api_person (name, kind, face_count, last_modified, share_location,
        share_other_people, cluster_owner_id)
    SELECT CASE WHEN a = 0 THEN 'Unknown - Other' ELSE 'Person ' || a END,
        CASE WHEN a = 0 THEN 'UNKNOWN' WHEN a < 300 THEN 'USER' ELSE 'CLUSTER' END,
        0, now(), false, false, %(uid)s
    FROM generate_series(0, 799) a
    """,
    {"uid": uid},
)
step(
    "clusters",
    """
    INSERT INTO api_cluster (cluster_id, name, mean_face_encoding, owner_id)
    SELECT a, 'c' || a, '00', %(uid)s FROM generate_series(0, 999) a
    """,
    {"uid": uid},
)
# ~1.6 faces per photo; 60%% labelled.
step(
    "faces",
    """
    INSERT INTO api_face (photo_id, cluster_probability, location_top, location_bottom,
        location_left, location_right, encoding, classification_probability, deleted,
        person_id, cluster_id, image)
    SELECT md5('p' || i)::uuid, 0.5, 0, 10, 0, 10, '00', 0.5, false,
        CASE WHEN (i + k) %% 10 < 6 THEN (SELECT min(id) FROM api_person) + (i + k) %% 800 END,
        (SELECT min(id) FROM api_cluster) + (i + k) %% 1000, 'faces/x.jpg'
    FROM generate_series(1, %(n)s) i CROSS JOIN generate_series(0, 2) k
    WHERE (i + k) %% 5 < 3 AND i %% 3 <> 1
    """,
    {"n": N},
)

# The second user: a small library, as on a shared server.
step(
    "small user photos",
    """
    INSERT INTO api_file (hash, path, type, missing)
    SELECT 's' || md5(i::text), '/data/small/' || i || '.jpg', 1, false FROM generate_series(1, 2000) i;
    INSERT INTO api_photo (id, image_hash, added_on, hidden, public, owner_id, video, rating,
        in_trashcan, size, last_modified, removed, local_orientation, is_screenshot, is_document,
        category_source, archived, ocr_failures, town, main_file_id)
    SELECT md5('s' || i)::uuid, 's' || md5(i::text), now(), false, false, %(sid)s, false, 0, false,
        1000, now(), false, 1, false, false, 'auto', false, 0, '', 's' || md5(i::text)
    FROM generate_series(1, 2000) i;
    INSERT INTO api_thumbnail (photo_id, thumbnail_big, square_thumbnail, square_thumbnail_small,
        aspect_ratio, video_poster)
    SELECT md5('s' || i)::uuid, 'b', 's', 'ss', 1.0, false FROM generate_series(1, 2000) i;
    """,
    {"sid": small.id},
)

step("analyze", "ANALYZE")
print(f"seeded {N} photos in {time.time() - t0:.0f}s")
