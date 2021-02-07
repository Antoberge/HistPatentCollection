import json
import yaml
import os
from glob import iglob
from smart_open import open
import git

from patentcity.relationship import create_relationship_component
import spacy
import typer

from patentcity.utils import clean_text, get_recid

"""
                             Brew patentcity dataset

General functioning: Stream text blobs | process through spaCy model | print json blobs to stdout
* Beta: entities only, no dependency parsing
"""

app = typer.Typer()
repo = git.Repo(search_parent_directories=True)
sha = repo.head.object.hexsha


def get_text(file, max_char=None):
    with open(file, "r") as fin:
        text = fin.read()[:max_char] if max_char else fin.read()
        publication_number = os.path.splitext(os.path.basename(file))[0]
        return " ".join([publication_number, text])


@app.command(deprecated=True)
def beta(
    path: str,
    model: str = None,
    max_char: int = None,
    reduced_perf: bool = False,
    inDelim: str = "|",
):
    """Print json blobs of the beta dataset

    {"publication_number": str,
    "pers": List[str],
    "org":List[str],
    "loc":List[Dict("raw":"", "recId":"")],
    "occ":List[str],
    "cit":List[str]}"""

    def serialize_blob(doc):
        publication_number, doc_ = doc[0].text, doc[1:]
        out = {"publication_number": publication_number}
        ents = doc_.ents
        labels = set([ent.label_ for ent in ents])
        for label in labels:
            out.update(
                {
                    label.lower(): [
                        {
                            "text": clean_text(ent.text, inDelim),
                            "start": ent.start,
                            "end": ent.end,
                        }
                        for ent in ents
                        if ent.label_ == label
                    ]
                }
            )
        if out.get("loc"):
            # from loc: ["", ""] to loc: [{"raw":"", "recId":""}, {...}]
            # -> should make it relatively efficient to integrate results back from here
            out.update(
                {
                    "loc": [
                        {
                            "raw": loc_["text"],
                            "recId": get_recid(loc_["text"]),
                            "start": loc_["start"],
                            "end": loc_["end"],
                        }
                        for loc_ in out["loc"]
                    ]
                }
            )
        typer.echo(json.dumps(out))

    nlp = spacy.load(model)
    blobs = iglob(path)
    texts = (get_text(file, max_char) for file in blobs)
    if reduced_perf:
        docs = nlp.pipe(texts, n_threads=1, batch_size=1)
    else:
        docs = nlp.pipe(texts)
    for doc in docs:
        serialize_blob(doc)


@app.command()
def v1(
    path: str,
    model: str,
    rel_config: str,
    batch_size: int = 1000,
    reduced_perf: bool = False,
    inDelim: str = "|",
):
    """
    Print jsonl blobs of the v1 dataset
    """
    with open(rel_config, "r") as config_file:
        config = yaml.load(config_file, Loader=yaml.FullLoader)

    nlp = spacy.load(model)
    nlp.add_pipe("relation_extractor", config={"config": config}, last=True)
    blobs = iglob(path)
    texts = (get_text(file) for file in blobs)
    if reduced_perf:
        docs = nlp.pipe(texts, n_threads=1, batch_size=1)
    else:
        docs = nlp.pipe(texts, batch_size=batch_size)
    for doc in docs:
        patentees = [
            {k: clean_text(v, inDelim) for k, v in patentee.items()}
            for patentee in doc._.patentees
        ]
        row = {
            "publication_number": doc[0].text,
            "patentee": patentees,
            "model_ents": model,
            "model_rels": rel_config,
            "git_sha": sha,
        }
        typer.echo(json.dumps(row))


if __name__ == "__main__":
    app()
