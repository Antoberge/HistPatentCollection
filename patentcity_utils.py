import datetime
import json
import os
import re
from glob import glob
from hashlib import md5

import numpy as np
import pandas as pd
import typer

from patentcity_lib import GEOC_OUTCOLS

"""
General purpose utils
"""

# msg utils
ok = "\u2713"
not_ok = "\u2717"

app = typer.Typer()
TAG_RE = re.compile(r"<[^>]+>")
WHITE_RE = re.compile(r"\s+")


def clean_text(text, inDelim=None):
    """Remove anchors <*> and </*> and replace by an empty space"""
    text = TAG_RE.sub(" ", text)
    text = WHITE_RE.sub(" ", text)
    text = text.replace("\n", " ")
    if inDelim:
        text = text.replace(inDelim, " ")
    return text


def get_dt_human():
    """Return current datetime for human (e.g. 23/07/2020 11:30:59)"""
    return datetime.datetime.now().strftime("%d/%m/%Y %H:%M:%S")


def get_recid(s, toint: bool = False):
    """Return the a uid made of the publication number and a random sequence 2^n random
    characters"""

    recid = md5(s.encode()).hexdigest()
    if toint:
        recid = int(recid, 16)
    return recid


def get_group(pubnum: int, u_bounds: str):
    u_bounds = [int(u_bound) for u_bound in u_bounds.split(",")]
    try:
        group = max(np.where(np.array(u_bounds) <= pubnum)[0]) + 2
    except ValueError:  # case where the pubnum is lower than any bound, hence in group 1
        group = 1
    return group


def get_pubnum(fname: str):
    try:
        pubnum = int(fname.split("-")[1])
    except (ValueError, IndexError):
        pubnum = None
        typer.secho(f"{not_ok}{fname} Publication number ill-formatted.")
    return pubnum


def get_empty_here_schema():
    return {k: None for k in GEOC_OUTCOLS}


def flatten(l):
    return [item for sublist in l for item in sublist]


@app.command()
def make_groups(path: str, u_bounds: str = None):
    """Distribute files in folders by groups. u_bounds (upper bounds of the groups) should be
    ascending & comma-separated."""
    files = glob(path)
    [
        os.mkdir(os.path.join(os.path.dirname(path), f"group_{i + 1}"))
        for i in range(len(u_bounds.split(",")) + 1)
    ]
    for file in files:
        fname = os.path.basename(file)
        if fname:
            pubnum = get_pubnum(fname)
            if pubnum:
                group = get_group(pubnum, u_bounds)
                dest = os.path.join(os.path.dirname(file), f"group_{group}", fname)
                os.rename(file, dest)
                typer.secho(f"{ok}Move {file}->group_{group}/", fg=typer.colors.GREEN)


@app.command()
def prep_annotation_groups(path: str, u_bounds: str = None):
    """Print files as proper prodigy jsonl input. Include with a 'format' field indicating the
    format the document belongs to."""

    files = glob(path)
    for file in files:  # could be multi threaded but not worth it
        fname = os.path.basename(file)
        # path = os.path.join(dir, fname)
        if fname:
            pubnum = get_pubnum(fname)
            publication_number = fname.replace(".txt", "")
            group = get_group(pubnum, u_bounds)
            with open(file, "r") as fin:
                out = {
                    "text": fin.read(),
                    "publication_number": publication_number,
                    "group": group,
                }
                typer.echo(json.dumps(out))


@app.command()
def get_whitespaced_labels(file: str):
    """Print info on labels with leading/trailing space.
    Expect a jsonl file with lines following a simple annotation model"""
    with open(file, "r") as lines:
        for i, line in enumerate(lines):
            line = json.loads(line)
            text = line["text"]
            spans = line.get("spans")
            if spans:
                for span in spans:
                    span_text = text[span["start"] : span["end"]]
                    startswith = span_text.startswith("\s")
                    endswith = span_text.endswith("\s")
                    if any([startswith, endswith]):
                        typer.secho(f"{json.dumps(line)}", fg=typer.colors.YELLOW)
                        typer.secho(
                            f"Span:{span}\nValue:{span_text}\nLine:{i + 1}",
                            fg=typer.colors.RED,
                        )


@app.command()
def model_report(model: str, pipes: str = "ner"):
    """Evaluate model"""

    scores = json.loads(open(os.path.join(model, "meta.json"), "r").read())["accuracy"]

    pipes = pipes.split(",")
    if "ner" in pipes:
        p, r, f = scores["ents_p"], scores["ents_r"], scores["ents_f"]
        typer.secho("NER Scores", fg=typer.colors.BLUE)
        typer.secho(f"{pd.DataFrame.from_dict(scores['ents_per_type'])}")
        typer.echo("-" * 37)
        typer.echo(f"ALL   %.2f  %.2f  %.2f" % (p, r, f))


@app.command()
def expand_pubdate_imputation(file: str, output: str = None):
    """Expand a sparse publication_date imputation file (with upper bound pubnum only) to a
    continuous (wrt pubnum) file."""
    df = pd.read_csv(file)
    max_pubnum = df.max()["pubnum"]
    expansion = pd.DataFrame(range(1, max_pubnum + 1), columns=["pubnum"])
    df_expansion = df.merge(expansion, how="right", left_on="pubnum", right_on="pubnum")
    df_expansion = df_expansion.fillna(method="backfill")
    for v in df_expansion.columns:
        df_expansion[v] = df_expansion[v].astype(int)
    df_expansion.to_csv(output, index=False)
    typer.secho(
        f"{ok} Imputation file expanded and saved in {output}", fg=typer.colors.GREEN
    )


@app.command()
def get_recid_nomatch(file, index, inDelim: str = "|"):
    """Retrieve the search text from recId which were not matched

    FILE is the HERE batch geocoding API output
    INDEX is the corresponding HERE batch geocoding API input
    """

    def get_search_text_index(index, inDelim):
        search_text_index = {}
        with open(index, "r") as lines:
            for line in lines:
                recid, searchtext = line.split(inDelim)
                search_text_index.update({recid: searchtext})
        return search_text_index

    search_text_index = get_search_text_index(index, inDelim)

    with open(file, "r") as lines:
        for line in lines:
            if "NOMATCH" in line:
                recid = line.split(",")[0]
                search_text = search_text_index.get(recid)
                typer.secho(f"{recid}{inDelim}{search_text}")
            else:
                pass


@app.command(deprecated=True)
def debug_duplicates(file: str, duplicates: str = None):
    """Update loc recId with md5 hashing (new get-recid) when it happens to be duplicated due to
    adler32 issue (old get_recid).
    """
    # E.g. 999425627|Checy (Frankreich) and 999425627|Ablon (Frankreich)
    list_duplicates = [
        int(dupl.replace("\n", "")) for dupl in list(open(duplicates, "r"))
    ]
    with open(file, "r") as lines:
        for line in lines:
            line = json.loads(line)
            updated_loc = []
            if line.get("loc"):
                for loc in line["loc"]:
                    if loc["recId"] in list_duplicates:
                        recid = get_recid(loc["raw"], toint=True)
                        # typer.secho(f"{loc['recId']}|{recid}", fg=typer.colors.YELLOW)
                        loc.update({"recId": recid})

                    updated_loc += [loc]
                line.update({"loc": updated_loc})
            typer.echo(json.dumps(line))


@app.command()
def remove_duplicates(
    file: str, inDelim: str = ",", duplicates: str = None, header: bool = True
):
    """Remove lines with adler32 duplicated recId from FILE"""
    list_duplicates = [
        int(dupl.replace("\n", "")) for dupl in list(open(duplicates, "r"))
    ]
    with open(file, "r") as lines:
        if header:
            line = next(lines)
            typer.echo(line)
        for line in lines:
            recid = int(line.split(inDelim)[0])
            if recid in list_duplicates:
                # typer.secho(line, fg=typer.colors.YELLOW)
                pass
            else:  # only lines where recId not in duplicates are preserved
                typer.echo(line)


# file = "data_tmp/de_locxx_beta_nomatch_here_sm_depr.txt"


@app.command()
def find_postcode(file, inDelim: str = "|", remove_postcodes: bool = True):
    with open(file, "r") as lines:
        for line in lines:
            recid, searchtext = line.split(inDelim)
            like_postcode = re.findall(r"\d{4}", searchtext)
            if like_postcode:
                if remove_postcodes:
                    for match in like_postcode:
                        searchtext = searchtext.replace(match, "")
                typer.echo(f"{recid}{inDelim}{searchtext}")


if __name__ == "__main__":
    app()
