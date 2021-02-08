import csv
import datetime
import json
import os
import re
import string
import sys
from concurrent.futures import ThreadPoolExecutor
from glob import glob
from hashlib import md5
from itertools import repeat
from operator import itemgetter
from pathlib import Path
import spacy

import numpy as np
import pandas as pd
import typer

# from fuzzyset import FuzzySet
from fuzzysearch import find_near_matches

from patentcity.lib import GEOC_OUTCOLS, get_isocrossover, list_countrycodes

"""
General purpose utilities
"""

# msg utils
ok = "\u2713"
not_ok = "\u2717"

app = typer.Typer()
TAG_RE = re.compile(r"<[^>]+>")
WHITE_RE = re.compile(r"\s+")

levels = [
    "country",
    "state",
    "county",
    "city",
    "postalCode",
    "district",
    "street",
    "houseNumber",
]


def clean_text(text, inDelim=None):
    """Remove anchors <*> and </*> and replace by an empty space"""
    if isinstance(text, str):
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
        group = int(max(np.where(np.array(u_bounds) <= pubnum)[0]) + 2)
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


def move_file(file, u_bounds):
    fname = os.path.basename(file)
    if fname:
        pubnum = get_pubnum(fname)
        if pubnum:
            group = get_group(pubnum, u_bounds)
            dest = os.path.join(os.path.dirname(file), f"group_{group}", fname)
            os.rename(file, dest)
            typer.secho(f"{ok}Move {file}->group_{group}/", fg=typer.colors.GREEN)


@app.command()
def make_groups(path: str, u_bounds: str = None, max_workers: int = 10):
    """Distribute files in folders by groups. u_bounds (upper bounds of the groups) should be
    ascending & comma-separated."""
    files = glob(path)
    [
        os.mkdir(os.path.join(os.path.dirname(path), f"group_{i + 1}"))
        for i in range(len(u_bounds.split(",")) + 1)
    ]

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        executor.map(move_file, files, repeat(u_bounds))


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
def get_buggy_labels(file: str):
    """Print info on labels with leading/trailing space.
    Expect a jsonl file with lines following a simple annotation model"""
    with open(file, "r") as lines:
        for i, line in enumerate(lines):
            line = json.loads(line)
            text = line["text"]
            spans = line.get("spans")
            punctuation_ = (
                string.punctuation.replace(".", "").replace("(", "").replace(")", "")
            )
            if spans:
                for span in spans:
                    span_text = text[span["start"] : span["end"]]
                    startswith_space = any(
                        [span_text.startswith(" "), span_text.startswith("\s")]
                    )
                    endswith_space = any(
                        [span_text.endswith("\s"), span_text.endswith(" ")]
                    )
                    startswith_punctuation, endswith_punctuation = ([], [])
                    for punctuation in punctuation_:
                        startswith_punctuation += [span_text.startswith(punctuation)]
                        endswith_punctuation += [span_text.endswith(punctuation)]
                    startswith_punctuation = any(startswith_punctuation)
                    endswith_punctuation = any(endswith_punctuation)
                    if any(
                        [
                            startswith_space,
                            endswith_space,
                            startswith_punctuation,
                            endswith_punctuation,
                        ]
                    ):
                        typer.secho(f"{json.dumps(line)}", fg=typer.colors.YELLOW)
                        typer.secho(
                            f"Span:{span}\nValue:{span_text}\nLine:{i + 1}",
                            fg=typer.colors.RED,
                        )


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


@app.command()
def prep_searchtext(
    file,
    inDelim: str = "|",
    remove_postcodes: bool = True,
    remove_countrycodes: bool = True,
):
    """Prepare search text so as to avoid common pitfalls (country codes, postcodes, etc)"""
    countrycodes = list_countrycodes()

    with open(file, "r") as lines:
        for line in lines:
            recid, searchtext = line.split(inDelim)
            like_postcode = re.findall(r"\b\d{4,}\b", searchtext)
            like_countrycode = re.findall(
                r"|".join(map(lambda x: r"(\b" + x + r")\b", countrycodes)), searchtext
            )
            if like_postcode:
                if remove_postcodes:
                    for match in like_postcode:
                        searchtext = searchtext.replace(match, "")
            if like_countrycode:
                like_countrycode = list(filter(lambda x: x, sum(like_countrycode, ())))
                if remove_countrycodes:
                    for match in like_countrycode:
                        searchtext = searchtext.replace(match, "")
            typer.echo(f"{recid}{inDelim}{searchtext}")


# def mcq(line, fset, ignore):
#     line = json.loads(line)  # .replace("\n", "").split(indelim)
#     if not line["recId"] in ignore:
#         text = line["text"]
#         closests = fset.get(text)
#         if closests:
#             closests = [closest[1] for closest in closests]
#             line.update(
#                 {
#                     "options": [
#                         {"id": closest, "text": closest} for closest in closests
#                     ],
#                     "accept": [closests[0]],
#                 }
#             )
#             typer.echo(json.dumps(line))
#         else:
#             pass
#     else:
#         pass
#
#
# @app.command()
# def mcq_factory(
#     loc: str = None, index: str = None, max_workers: int = 5, list_ignore: str = None
# ):
#     """Return jsonl for choice prodigy view-id based on fuzzyset suggestion for each line based
#     on the text of each line in the loc file and the targets in the index file"""
#     targets = open(index, "r").read().split("\n")
#     fset = FuzzySet()
#     for target in targets:
#         fset.add(target)
#     if list_ignore:
#         with open(list_ignore, "r") as ignore:
#             ignore = ignore.read().replace('"', "").split("\n")
#     else:
#         ignore = []
#     with open(loc, "r") as lines:
#         with ThreadPoolExecutor(max_workers=max_workers) as executor:
#             executor.map(mcq, lines, repeat(fset), repeat(ignore))


@app.command()
def mcq_revert(file, max_options: int = 50):
    """Revert a sequence of mcq where main text is the raw text and options are
    targets into a sequence of mcq where the target is the main text and options are the raw text"""

    def revert_line(line, index):
        for option in line.get("options"):
            text = option["id"]
            if not index.get(text):
                index.update({text: []})
            text_options = index[text] + [
                {
                    "id": line["recId"],
                    "text": line["text"],
                    "nb_occurences": line["nb_occurences"],
                    "eg_publication_number": line["eg_publication_number"],
                }
            ]
            index.update({text: text_options})
            return index

    index = {}
    with open(file, "r") as lines:
        for line in lines:
            line = json.loads(line)
            index = revert_line(line, index)

    tasks = []
    for text, options in index.items():
        options = sorted(options, key=itemgetter("nb_occurences"), reverse=True)
        # if len(options) > max_options:
        for chunk_options in [
            options[x : x + max_options] for x in range(0, len(options), max_options)
        ]:
            tasks += [
                {
                    "text": text,
                    "nb_occurences": max(
                        [opt["nb_occurences"] for opt in chunk_options]
                    ),
                    "options": chunk_options,
                }
            ]
    tasks = sorted(tasks, key=itemgetter("nb_occurences"), reverse=True)
    for task in tasks:
        typer.echo(json.dumps(task))


@app.command()
def prep_disamb_index(file: str, inDelim: str = "|"):
    """Return a list of recId(line)|line from a list of lines
    Should be used to prepare a list of disambiguation targets for downstream process (e.g.
    add-geoc-disamb"""
    with open(file, "r") as lines:
        for line in lines:
            line = clean_text(line, inDelim)
            recid = get_recid(line)
            typer.echo(f"{recid}{inDelim}{line}")


@app.command()
def add_geoc_disamb(disamb_file, index_geoc_file, inDelim: str = "|"):
    """Return a list of recId|geoc(target) from a list of recid|target.
    Should be used before add-geoc-data"""
    index = {}
    with open(index_geoc_file, "r") as lines:
        for line in lines:
            recid, geoc = line.split(inDelim)
            index.update({recid: json.loads(geoc)})

    with open(disamb_file, "r") as lines:
        for line in lines:
            recid, disamb_loc = line.split(inDelim)
            disamb_loc_recid = get_recid(clean_text(disamb_loc))
            typer.echo(f"{recid}{inDelim}{json.dumps(index.get(disamb_loc_recid))}")


@app.command()
def prep_disamb(file: str, orient: str = "revert", inDelim: str = "|"):
    """Prep disamb file from mcq output data
    """
    assert orient in ["revert"]
    with open(file, "r") as lines:
        for line in lines:
            line = json.loads(line)
            accepts = line.get("accept")
            text = line.get("text")
            for accept in accepts:
                typer.echo(f"{accept}{inDelim}{text}")


@app.command()
def generate_iso_override():
    """Generate the iso override file"""
    csvwriter = csv.DictWriter(sys.stdout, GEOC_OUTCOLS)
    countrycodes = list_countrycodes()
    iso_crossover = get_isocrossover()
    csvwriter.writeheader()
    for countrycode in countrycodes:
        out = get_empty_here_schema()
        country = countrycode if len(countrycode) == 3 else iso_crossover[countrycode]
        out.update(
            {"recId": get_recid(countrycode), "seqNumber": 1, "country": country}
        )
        csvwriter.writerow(out)


@app.command()
def get_gmaps_index_wgp(file: str, inDelim: str = "|", verbose: bool = False):
    """
    Return the csv file as the gmaps geoc index we are using in patentcity
    recId|{gmaps output}
    Next, it should be harmonized with HERE data.
    E.g. addresses_florian25.csv
    """
    with open(file, "r") as fin:
        csv_reader = csv.reader(fin, delimiter=",", escapechar="\\")
        for line in csv_reader:
            try:
                typer.echo(
                    f"{line[0]}{inDelim}{json.dumps(json.loads(line[3])['results'])}"
                )
            except Exception as e:
                if verbose:
                    typer.secho(str(e), fg=typer.colors.RED)
                pass
            # the first field is the location id (eq recid)
            # the second field is the gmaps result


def get_cit_code(text: str, fst: dict, fuzzy_match: bool):
    """Return the ISO-3 country code of the detected citizenship

    If fuzzy match True, fuzzy match considered iff exact match None
    """

    def get_candidates(text: str, fst: dict, fuzzy_match: bool = True):
        # we start by considering only exact matches here
        text = text.lower()
        candidates = [key for key in fst.keys() if key.lower() in text.lower()]
        if not candidates and fuzzy_match:
            text = text.replace("laws", "")  # creates confusion with Laos
            candidates = [
                key
                for key in fst.keys()
                if find_near_matches(key.lower(), text.lower(), max_l_dist=1)
            ]
        return candidates

    def get_pred(candidates: list, fst: dict):
        candidates = [fst[candidate] for candidate in candidates]
        if len(set(candidates)) == 1:
            pred = candidates[0]
        elif len(candidates) > 1:
            # in case there are more than 1 candidates, we take the most frequent
            if "USA" in candidates and "JEY" in candidates:
                pred = "USA"
            elif "USA" in candidates and "NIU" in candidates:
                pred = "USA"
            elif "USA" in candidates and "IND" in candidates:
                pred = "USA"
            elif "DDR" in candidates and "DEU" in candidates:
                pred = "DDR"
            else:
                pred = max(candidates, key=candidates.count)
            # will randomly pick one if multi-max
        else:
            pred = None

        return pred

    candidates = get_candidates(text, fst, fuzzy_match)
    pred = get_pred(candidates, fst)

    return pred


class ReportFormat:
    def __init__(
        self,
        file: Path,
        bins: iter,
        lang: str = None,
        crop: bool = True,
        md: bool = False,
    ):
        self.file = file
        self.bins = bins
        self.lang = lang
        self.crop = crop
        self.md = md
        self.nlp = spacy.blank(self.lang) if self.lang else self.lang
        self.unit = "tok" if self.lang else "char"

    def hist_to_stdout(self, a):
        hist_vals, hist_bins = np.histogram(a, bins=self.bins)
        if self.crop:
            nonzero = np.where(hist_vals != 0)
            hist_vals, hist_bins = hist_vals[nonzero], hist_bins[nonzero]

        if self.md:
            typer.echo("```shell script")
        for i in range(len(hist_vals)):
            typer.echo(
                f"{hist_bins[i]}\t" + "+" * (hist_vals / len(a) * 100).astype(int)[i]
            )
        if self.md:
            typer.echo("```")

    def get_data(self):
        # typer.echo("Acquiring data ...")
        with self.file.open("r") as lines:
            lengths = []
            span_starts = []
            for line in lines:
                line = json.loads(line)
                text = line["text"]
                spans = line.get("spans")
                if self.unit == "tok":
                    span_starts += [span["token_start"] for span in spans]
                else:
                    span_starts += [span["start"] for span in spans]

                if self.unit == "tok":
                    doc = self.nlp(text)
                    lengths += [len(doc)]
                else:
                    lengths += [len(text)]
            return lengths, span_starts

    def get_report(self):
        lengths, span_starts = self.get_data()
        typer.secho(f"# REPORT `{os.path.basename(self.file)}`", fg=typer.colors.BLUE)
        typer.secho(f"\n> ℹ️ Unit: {self.unit}\n", fg=typer.colors.BLUE)
        typer.secho("\n## Doc lengths\n", fg=typer.colors.BLUE)
        self.hist_to_stdout(lengths)
        typer.secho("\n## Span starts\n", fg=typer.colors.BLUE)
        self.hist_to_stdout(span_starts)


@app.command()
def report_format(
    path: str,
    bins: str = "0,2000,50",
    lang: str = None,
    crop: bool = True,
    md: bool = True,
):
    """
    Return the histogram of doc length and start span
    """
    files = list(Path().glob(path))
    start, end, by = list(map(int, bins.split(",")))
    bins = np.arange(start, end, by)
    for file in files:
        report = ReportFormat(file, bins=bins, lang=lang, crop=crop, md=md)
        report.get_report()


@app.command()
def prep_geoc_gold(gold: str, data: str):
    """Return a csv file with gold annotations"""

    def accept2array(x):
        idx = levels.index(x) if x else None
        if idx:
            truth_array = [1] * (idx + 1) + [0] * (len(levels) - (idx + 1))
        else:
            truth_array = [0] * len(levels)
        return truth_array

    gold_df = pd.read_json(gold, lines=True)
    data_df = pd.read_json(data, lines=True)

    gold_ = gold_df.query("answer=='accept'")[["loc_text", "accept"]]
    gold_["accept"] = gold_["accept"].apply(lambda x: x[0] if x else None)

    out = data_df.merge(gold_, on="loc_text", how="left")
    labels = pd.DataFrame(
        data=out["accept"].apply(accept2array).values.tolist(), columns=levels
    )

    typer.echo(out.merge(labels, right_index=True, left_index=True).to_csv())


if __name__ == "__main__":
    app()
