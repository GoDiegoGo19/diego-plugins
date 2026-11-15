#!/usr/bin/env python3
"""p4_sort_families.py · P4 SORT (v16) · look-alike segments -> FAMILIES, and faces -> PEOPLE, across the whole drive.

No model here: the fingerprints from P3 SEE are numbers, so sorting is plain, deterministic math (NumPy only).

    python3 p4_sort_families.py --drive <drive> [--catalog DIR] [--db PATH] [--no-db]

FAMILIES (rules.json -> vision.family)
  1. leader walk  segments in drive order (card, clip, time); join the closest family if similarity to its centre is
                  at least join_above, else start a new one. Centre = normalized running SUM with counts.
  2. frozen pass  centres frozen; every segment moves to its closest centre (or starts its own when none reaches
                  join_above). Decisions in this pass never depend on each other.
  3. compactness  a member below min_compactness to its family's centre leaves as a singleton (no chain merges:
                  A~B and B~C never makes A~C). Singletons are valid results, not errors.
  name = majority content + shot tag of the members (CHECK when they are unsure); hero = lit member nearest the centre.
PEOPLE: face vectors (SFace) grouped the same way with vision.faces.same_person_above; each segment lists its people.

Writes (nothing deleted; the F4 database rule: built in temp, copied in once, checked):
  <catalog>/<drive>/P4_STAGING/<YYMMDD>_<drive>_sort_families_P4-02_<HHMM>.json
  <catalog>/catalog_objects.sqlite  tables `segments`, `families`, `people` (this drive's rows replaced)
"""
import argparse
import collections
import datetime as dt
import json
import os
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import p0_env  # noqa: E402
import p3_see_models as sm  # noqa: E402

SCHEMA = """
CREATE TABLE IF NOT EXISTS segments (seg_id TEXT PRIMARY KEY, drive TEXT, clip_id TEXT, rel_path TEXT, card TEXT, shot INTEGER,
  n INTEGER, t_in REAL, t_out REAL, hero_t REAL, thumb TEXT, content TEXT, content_p REAL, shot_size TEXT, shot_p REAL,
  light TEXT, light_p REAL, people INTEGER, people_h REAL, faces INTEGER, persons TEXT, text TEXT, caption TEXT,
  family TEXT, family_sim REAL, status TEXT, why TEXT);
CREATE TABLE IF NOT EXISTS families (family TEXT, drive TEXT, name TEXT, content TEXT, shot_size TEXT, light TEXT,
  segments INTEGER, seconds REAL, clips INTEGER, cards INTEGER, hero_seg TEXT, hero_thumb TEXT, cohesion REAL,
  PRIMARY KEY (drive, family));
CREATE TABLE IF NOT EXISTS people (person TEXT, drive TEXT, faces INTEGER, segments INTEGER, clips INTEGER, hero_seg TEXT,
  PRIMARY KEY (drive, person));
"""


def group(V, join, min_comp):
    """Leader walk -> frozen-centroid pass -> compactness. Returns (assignment, unit centres)."""
    np = sm.np_()
    if not len(V):
        return [], np.zeros((0, V.shape[1] if V.ndim == 2 else 1), "float32")
    sums, counts, assign = [V[0].copy()], [1], [0]
    for v in V[1:]:
        s = sm.unit(np.stack(sums)) @ v
        j = int(s.argmax())
        if s[j] >= join:
            sums[j] += v
            counts[j] += 1
            assign.append(j)
        else:
            sums.append(v.copy())
            counts.append(1)
            assign.append(len(sums) - 1)
    C = sm.unit(np.stack(sums))                                  # frozen
    S = V @ C.T
    best = S.argmax(1)
    new, nxt = [], len(C)
    for i, b in enumerate(best):
        if S[i, b] >= join:
            new.append(int(b))
        else:
            new.append(nxt)
            nxt += 1
    used = sorted(set(new))
    remap = {o: k for k, o in enumerate(used)}
    assign = [remap[x] for x in new]
    C = sm.unit(np.stack([V[[i for i, a in enumerate(assign) if a == k]].sum(0) for k in range(len(used))]))
    nxt = len(C)
    for i, a in enumerate(assign):                               # compactness: leave as a singleton, never pull others along
        if (assign.count(a) > 1) and float(V[i] @ C[a]) < min_comp:
            assign[i] = nxt
            nxt += 1
    used = sorted(set(assign))
    remap = {o: k for k, o in enumerate(used)}
    assign = [remap[x] for x in assign]
    C = sm.unit(np.stack([V[[i for i, a in enumerate(assign) if a == k]].sum(0) for k in range(len(used))]))
    return assign, C


def majority(values):
    sure = [x for x in values if x and x != "CHECK"]
    if not sure:
        return "CHECK"
    top, n = collections.Counter(sure).most_common(1)[0]
    return top if n * 2 > len(values) else "mixed"


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--drive", required=True)
    ap.add_argument("--catalog", default=str(p0_env.catalog()))
    ap.add_argument("--db", default=None)
    ap.add_argument("--no-db", action="store_true")
    a = ap.parse_args()
    t0 = time.time()
    np = sm.np_()
    v = sm.V()
    catalog = Path(os.path.abspath(os.path.expanduser(a.catalog)))
    drive = a.drive
    see = p0_env.newest(catalog, drive, "P3", "see", "segments", "json")
    if not see:
        print("sort: SKIPPED · no P3 SEE output for %s" % drive)
        return 4
    doc = json.load(open(see, encoding="utf-8"))
    z = np.load(see.with_name(doc["vectors"]), allow_pickle=False)
    ids = [str(x) for x in z["ids"]]
    fp = dict(zip(ids, z["fp"]))
    face_seg, face_vec = [str(x) for x in z["face_seg"]], z["face_vec"]

    segs, pending = [], []
    for c in sorted(doc["clips"], key=lambda c: (c.get("group", ""), c["rel_path"])):
        if c.get("status") != "seen":
            pending.append({"rel_path": c["rel_path"], "card": c.get("group", ""), "why": c.get("why", "")})
            continue
        segs += [(c, s) for s in c["segments"] if s["seg_id"] in fp]
    Vm = np.stack([fp[s["seg_id"]] for _, s in segs]) if segs else np.zeros((0, 1), "float32")
    fr = v["family"]
    assign, C = group(Vm, float(fr["join_above"]), float(fr.get("min_compactness", 0)))

    # people: faces grouped across the drive
    persons_of = collections.defaultdict(set)
    people_rows = []
    if len(face_vec):
        pa, PC = group(face_vec.astype("float32"), float(v["faces"]["same_person_above"]), 0.0)
        by = collections.defaultdict(list)
        for i, k in enumerate(pa):
            by[k].append(i)
        order = sorted(by, key=lambda k: -len(by[k]))
        pid = {k: "P%02d" % (n + 1) for n, k in enumerate(order)}
        seg_clip = {s["seg_id"]: c["rel_path"] for c, s in segs}
        for k in order:
            sids = [face_seg[i] for i in by[k]]
            for s in sids:
                persons_of[s].add(pid[k])
            people_rows.append({"person": pid[k], "faces": len(sids), "segments": len(set(sids)),
                                "clips": len({seg_clip.get(s) for s in sids}), "hero_seg": sids[0]})

    members = collections.defaultdict(list)
    for i, k in enumerate(assign):
        members[k].append(i)
    secs = lambda i: segs[i][1]["t_out"] - segs[i][1]["t_in"]
    order = sorted(members, key=lambda k: (-sum(secs(i) for i in members[k]), min(members[k])))
    fid = {k: "F%02d" % (n + 1) for n, k in enumerate(order)}
    families, rows = [], []
    for k in order:
        idx = members[k]
        sims = [float(Vm[i] @ C[k]) for i in idx]
        lit = [(s_, i) for s_, i in zip(sims, idx) if not segs[i][1].get("dark") and segs[i][1].get("thumb")]
        hero = max(lit or list(zip(sims, idx)))[1]
        tg = lambda ax: [segs[i][1]["tags"][ax]["value"] for i in idx]
        content, shot, light = majority(tg("content")), majority(tg("shot")), majority(tg("light"))
        families.append({"family": fid[k], "name": " · ".join(x for x in (content, shot) if x != "mixed") or "mixed",
                         "content": content, "shot_size": shot, "light": light, "segments": len(idx),
                         "seconds": round(sum(secs(i) for i in idx), 1), "clips": len({segs[i][0]["rel_path"] for i in idx}),
                         "cards": len({segs[i][0].get("group", "") for i in idx}), "hero_seg": segs[hero][1]["seg_id"],
                         "hero_thumb": ("P3_PROCESS/" + segs[hero][1]["thumb"]) if segs[hero][1].get("thumb") else "",
                         "cohesion": round(sum(sims) / len(sims), 3)})
    for i, (c, s) in enumerate(segs):
        t = s["tags"]
        unsure = [ax for ax in t if t[ax]["value"] == "CHECK"]
        cap = s.get("caption") or {}
        rows.append({"seg_id": s["seg_id"], "clip_id": c["clip_id"], "rel_path": c["rel_path"], "card": c.get("group", ""),
                     "file_name": c.get("file_name", ""), "shot": s.get("shot"), "n": s["n"], "t_in": s["t_in"],
                     "t_out": s["t_out"], "hero_t": s.get("hero_t"),
                     "thumb": ("P3_PROCESS/" + s["thumb"]) if s.get("thumb") else "",
                     "content": t["content"]["value"], "content_p": t["content"]["p"], "shot_size": t["shot"]["value"],
                     "shot_p": t["shot"]["p"], "light": t["light"]["value"], "light_p": t["light"]["p"],
                     "people": s["people"].get("count"), "people_h": s["people"].get("biggest_h"),
                     "faces": s.get("faces"), "persons": " ".join(sorted(persons_of.get(s["seg_id"], ()))),
                     "text": " | ".join(s.get("text") or []), "caption": cap.get("value", "") if isinstance(cap, dict) else "",
                     "family": fid[assign[i]], "family_sim": round(float(Vm[i] @ C[assign[i]]), 3),
                     "status": "unresolved" if unsure else "verified",
                     "why": "; ".join("%s: best guess %s (p %.2f)" % (ax, t[ax]["best"], t[ax]["p"]) for ax in unsure)})
    # ---- round 2 layers: HDBSCAN challenger, keyword names (BERTopic + TF-IDF), zero-shot label. Each its own column.
    tv = dict(zip(ids, z["tv"]))
    hd = ["noise"] * len(segs)
    if len(segs) >= 4:
        try:
            from sklearn.cluster import HDBSCAN
            from sklearn.decomposition import PCA
            X = PCA(n_components=min(int(v["topics"]["pca_dims"]), len(segs) - 1, Vm.shape[1])).fit_transform(Vm)
            hd = ["H%02d" % l if l >= 0 else "noise" for l in HDBSCAN(min_cluster_size=int(v["topics"]["min_cluster_size"])).fit_predict(X)]
        except Exception as e:
            print("sort: HDBSCAN failed (%s)" % str(e)[:80])
    for r_, h in zip(rows, hd):
        r_["family_hdbscan"] = h
    docs = []
    for c, s_ in segs:
        words = [s_["tags"][ax]["value"] for ax in s_["tags"] if s_["tags"][ax]["value"] != "CHECK"]
        words += list(s_.get("text") or [])
        cap = s_.get("caption") or {}
        if isinstance(cap, dict) and cap.get("value") and cap.get("value") != "PENDING":
            words.append(cap["value"])
        for t in (c.get("transcript") or {}).get("segments", []):
            if t["t_out"] > s_["t_in"] and t["t_in"] < s_["t_out"]:
                words.append(t["text"])
        docs.append(" ".join(words))
    nwords = [len(d.split()) for d in docs]
    topics_note = {"median_words_per_segment": sorted(nwords)[len(nwords) // 2] if nwords else 0,
                   "min_text_words": int(v["topics"]["min_text_words"])}
    fam_kw_tfidf, fam_kw_bert, fam_label = {}, {}, {}
    try:                                                      # TF-IDF keywords per family (Gemini's route)
        from sklearn.feature_extraction.text import TfidfVectorizer
        fam_docs = collections.defaultdict(list)
        for i, k in enumerate(assign):
            fam_docs[fid[k]].append(docs[i])
        names_ = list(fam_docs)
        tf = TfidfVectorizer(stop_words="english", ngram_range=(1, 2))
        M_ = tf.fit_transform([" ".join(fam_docs[n]) for n in names_])
        vocab = tf.get_feature_names_out()
        for j, n in enumerate(names_):
            row = M_[j].toarray()[0]
            fam_kw_tfidf[n] = [vocab[x] for x in row.argsort()[::-1][: int(v["topics"]["top_words"])] if row[x] > 0]
    except Exception as e:
        topics_note["tfidf"] = "failed: %s" % str(e)[:80]
    try:                                                      # BERTopic on our own MiniLM text fingerprints (no PyTorch)
        ok_idx = [i for i, n_ in enumerate(nwords) if n_ >= topics_note["min_text_words"]]
        if len(ok_idx) >= 5:
            from bertopic import BERTopic
            from sklearn.cluster import HDBSCAN
            from sklearn.decomposition import PCA
            E = sm.embed_text("minilm", [docs[i] for i in ok_idx])
            bt = BERTopic(embedding_model=None, umap_model=PCA(n_components=min(5, len(ok_idx) - 1)),
                          hdbscan_model=HDBSCAN(min_cluster_size=int(v["topics"]["min_cluster_size"])),
                          calculate_probabilities=False)
            tops, _ = bt.fit_transform([docs[i] for i in ok_idx], embeddings=E)
            for i, t in zip(ok_idx, tops):
                rows[i]["topic_bertopic"] = int(t)
                if t >= 0:
                    rows[i]["topic_keywords"] = " · ".join(w for w, _ in bt.get_topic(t)[: int(v["topics"]["top_words"])])
            for k in order:
                ts = [rows[i].get("topic_keywords") for i in members[k] if rows[i].get("topic_keywords")]
                if ts:
                    fam_kw_bert[fid[k]] = collections.Counter(ts).most_common(1)[0][0]
        else:
            topics_note["bertopic"] = "skipped: %d segments have >= %d words (needs 5)" % (len(ok_idx), topics_note["min_text_words"])
    except Exception as e:
        topics_note["bertopic"] = "failed: %s" % str(e)[:100]
    try:                                                      # nearest word-list label to each family centre (zero-shot)
        ch_ = doc.get("choose") or v["choose"]
        bank = sm.tag_bank(sm.tagger_keys(ch_["tagger"])[1])
        for k in order:
            cen = sm.unit(np.stack([tv[segs[i][1]["seg_id"]] for i in members[k]]).mean(0))
            fam_label[fid[k]] = sm.tag(cen, {"content": bank["content"]})["content"]["best"]
    except Exception as e:
        topics_note["zero_shot"] = "failed: %s" % str(e)[:80]
    for f_ in families:
        f_["keywords_tfidf"] = " · ".join(fam_kw_tfidf.get(f_["family"], []))
        f_["keywords_bertopic"] = fam_kw_bert.get(f_["family"], "")
        f_["label_zero_shot"] = fam_label.get(f_["family"], "")
        f_["hdbscan_mix"] = dict(collections.Counter(hd[i] for i in members[[k for k in order if fid[k] == f_["family"]][0]]))
    clip_extra = {c["rel_path"]: {k: c.get(k) for k in ("shots", "cuts_transnet", "speech", "transcript", "transcribe_tag")}
                  for c in doc["clips"]}
    out = p0_env.out_path(catalog, drive, "P4", "sort", "families", 2, "json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump({"drive": drive, "made": dt.datetime.now().isoformat(timespec="seconds"), "see": see.name,
                   "pass": doc.get("pass"), "only": doc.get("only", ""), "choose": doc.get("choose"), "rules": fr,
                   "families": families, "people": people_rows, "segments": rows, "clips": clip_extra, "topics": topics_note,
                   "pending": pending}, f, indent=1, ensure_ascii=False)
    if not a.no_db:
        import p4_staging_objects as p4                     # database helpers: temp build, copy in once, check (F4)
        db = p4.open_db(p0_env.catalog_db(catalog, a.db))
        for tbl in ("segments", "families", "people"):       # v16 widened these tables: rebuild them (rows, not files)
            cols = [r[1] for r in db.execute("PRAGMA table_info(%s)" % tbl)]
            if cols and tbl == "segments" and "persons" not in cols:
                db.execute("DROP TABLE %s" % tbl)
        db.executescript(SCHEMA)
        for tbl in ("segments", "families", "people"):
            db.execute("DELETE FROM %s WHERE drive = ?" % tbl, (drive,))
        sc = ["seg_id", "drive", "clip_id", "rel_path", "card", "shot", "n", "t_in", "t_out", "hero_t", "thumb", "content",
              "content_p", "shot_size", "shot_p", "light", "light_p", "people", "people_h", "faces", "persons", "text",
              "caption", "family", "family_sim", "status", "why"]
        db.executemany("INSERT INTO segments VALUES (%s)" % ",".join("?" * len(sc)),
                       [tuple(drive if k == "drive" else r.get(k) for k in sc) for r in rows])
        fc = ["family", "drive", "name", "content", "shot_size", "light", "segments", "seconds", "clips", "cards",
              "hero_seg", "hero_thumb", "cohesion"]
        db.executemany("INSERT INTO families VALUES (%s)" % ",".join("?" * len(fc)),
                       [tuple(drive if k == "drive" else f_.get(k) for k in fc) for f_ in families])
        db.executemany("INSERT INTO people VALUES (?,?,?,?,?,?)",
                       [(p["person"], drive, p["faces"], p["segments"], p["clips"], p["hero_seg"]) for p in people_rows])
        p4.close_db(db)
    print("sort: %s · %d segments -> %d families · %d people · %d need a look · %d clips pending · %.0f s -> %s" % (
        drive, len(rows), len(families), len(people_rows), sum(1 for r in rows if r["status"] != "verified"),
        len(pending), time.time() - t0, out.name))
    return 0


if __name__ == "__main__":
    sys.exit(main())
