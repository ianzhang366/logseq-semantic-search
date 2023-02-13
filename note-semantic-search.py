# a fork of https://github.com/robertmartin8/MolecularNotes/blob/master/_scripts/gpt_search.py
# trying to make it work with logseq

import time
import os
import re
import urllib
import pickle
import warnings

import openai
import numpy as np
import pandas as pd
import tiktoken
import click

from tabulate import tabulate


# CONFIG
openai.api_key = "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"

OUTPUT_DIR = "./_scripts"
DF_FILE = f"{OUTPUT_DIR}/embeddings.csv"
CACHE_FILE = f"{OUTPUT_DIR}/query_cache.pkl"

NOTES_ROOT_DIR = "/Users/ianzhang/pnotes"


###############
# OPENAI CODE #
###############
EMBEDDING_MODEL = "text-embedding-ada-002"
COST_PER_TOKEN = 0.0004 / 1000
EMBEDDING_CTX_LENGTH = 8191
EMBEDDING_ENCODING = "cl100k_base"

# Calculate the delay based on your rate limit
# https://github.com/openai/openai-cookbook/blob/main/examples/How_to_handle_rate_limits.ipynb
rate_limit_per_minute = 20
DEPLAY_PER_REQUEST = 60.0 / rate_limit_per_minute


def num_tokens_from_string(string: str, encoding_name=EMBEDDING_ENCODING) -> int:
    """Returns the number of tokens in a text string."""
    encoding = tiktoken.get_encoding(encoding_name)
    num_tokens = len(encoding.encode(string))
    return num_tokens


def truncate_text_tokens(
    text: str, encoding_name=EMBEDDING_ENCODING, max_tokens=EMBEDDING_CTX_LENGTH
):
    """Truncate a string to have `max_tokens` according to the given encoding."""
    encoding = tiktoken.get_encoding(encoding_name)
    return encoding.encode(text)[:max_tokens]


def cosine_similarity(a: np.ndarray, b: np.ndarray) -> np.float64:
    return np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b))


def get_embedding(delay_in_seconds: int, block: str) -> list:
    """Delay a completion by a specified amount of time."""
    # Sleep for the delay
    time.sleep(delay_in_seconds)

    return openai.Embedding.create(input=block, model=EMBEDDING_MODEL)["data"][0]["embedding"]


#################################
# NOTES FILE PREPROCESSING #
#################################


def extract_sections(file_path: str) -> dict[str, str]:
    # For a given markdown note, make a dict mapping headers to content.
    sections = {}
    image_as_block = "^\s*!\[.*\.(png|jpg)\]\(.*\)"
    with open(file_path, "r") as file:
        content = file.read().split("\n")
        section = "no_title"
        sections[section] = ""
        for line in content:
            ismatch = re.match(image_as_block, line)
            if ismatch:
                continue

            if line.startswith("- "):
                section = line
                section = section.lstrip('#').strip()
                section = section.lstrip('- ').strip()

                if section not in sections:
                    sections[section] = ""
            else:
                sections[section] += line + "\n"
    return sections


def clean_section(txt: str) -> str:
    # Clean a text block, removing frontmatter, formatting, empty lines.
    # if "#atom" in txt or "#molecule" in txt:
    #     txt = txt.split("---")[0]
    # elif "#source" in txt:
    #     txt = txt.split("---")[1]

    txt = re.sub(r"\[(.*?)\]\((.*?)\)", r"\1", txt)

    repl = ["[[", "]]", "*", "`", "*"]
    for r in repl:
        txt = txt.replace(r, "")

    repl_space = ["\n", "\t", "\xa0", "  "]
    for r in repl_space:
        txt = txt.replace(r, " ")
    txt = txt.replace("\\\\", "\\")

    txt = txt.lstrip().rstrip()
    return txt


ODSIDIAN_SKIP_LIST = ["_templates", "_scripts",
                      ".obsidian", "__Canvases", ".git", "_attachments"]
LOGSEQ_SKIP_LIST = ["assets", "draw", "draws", "logseq", "0-photoes", "photo"]


def read_markdown_notes(folder_path: str) -> dict[str, dict[str, str]]:
    # Iterate through vault, making a dictionary of {(filename, chapter): text}
    notes = {}
    for root, dirs, files in os.walk(folder_path):
        dirs[:] = [subdir for subdir in dirs if subdir not in LOGSEQ_SKIP_LIST]

        if any(f in LOGSEQ_SKIP_LIST for f in root.split(os.sep)[-len(LOGSEQ_SKIP_LIST):]):
            continue

        for file in files:
            if file.endswith(".md"):
                file_path = os.path.join(root, file)

                # Filter out topic files
                with open(file_path, "r") as f:
                    md = f.read()
                if any(x in md for x in ["#topic", "#author"]):
                    continue

                # Clean files
                try:
                    sections = extract_sections(file_path)
                    for section_id, section_contents in sections.items():
                        cleaned_txt = clean_section(section_contents)
                        if cleaned_txt == "":
                            continue
                        notes[(file_path.lstrip("./"), section_id)] = cleaned_txt
                except Exception as e:
                    print(f"Parse file Error for {file_path}", e)
                    raise

    return notes


def get_obsidian_uri(filename: str, section_name: str) -> str:
    # Given a filename and a section_name, return the advanced-uri plugin's URI so that I can click a link to the file.
    if section_name == "":
        return f"obsidian://advanced-uri?vault=ObsidianVault&filepath={urllib.parse.quote(filename, safe='')}"
    else:
        return f"obsidian://advanced-uri?vault=ObsidianVault&filepath={urllib.parse.quote(filename, safe='')}&heading={urllib.parse.quote(section_name, safe='')}"


##############
# CORE LOGIC #
##############


def estimate_cost(notes: dict[(str, str), str]):
    # Counts the number of tokens to estimate the cost.
    notecount = len(set(i[0] for i in notes.keys()))
    sectioncount = len(notes)
    tokencount = 0
    for (note, section), text in notes.items():
        block = section + ". " + text
        tokencount += num_tokens_from_string(block)

    cost = tokencount * COST_PER_TOKEN
    click.echo(
        f"{notecount} notes; {sectioncount} blocks; {tokencount} tokens => cost = "
        + click.style(f"${cost:.4f}", fg="red")
    )
    click.confirm(
        "This will overwrite and rebuild embeddings. Confirm?", abort=True)


def ensure_output_dir_exist():
    folder_path = OUTPUT_DIR
    if not os.path.isdir(folder_path):
        print(
            f"The folder {folder_path} does not exist, will create one for you")
        os.makedirs(folder_path)


def build_embeddings(df_file=DF_FILE):
    # get all notes
    notes = read_markdown_notes(NOTES_ROOT_DIR)
    # print cost report and confirm
    estimate_cost(notes)
    ensure_output_dir_exist()
    # Embed and save
    df = embed(notes)
    click.echo("Saving df.")
    df.to_csv(DF_FILE)


def read_df_file(df_file=DF_FILE) -> pd.DataFrame:
    # Util needed since some of my multi-index entries are empty strings.
    df = pd.read_csv(DF_FILE, header=[0, 1])
    df.columns = pd.MultiIndex.from_tuples(
        [tuple(["" if y.find("Unnamed") == 0 else y for y in x])
         for x in df.columns]
    )
    return df


def update_embeddings(df_file=DF_FILE):
    # get all notes
    notes = read_markdown_notes(NOTES_ROOT_DIR)

    # read df
    df = read_df_file(df_file)

    # filter to only get notes not in df.columns
    new_notes = {k: v for k, v in notes.items() if k not in df.columns}

    # print cost report and confirm with user
    estimate_cost(new_notes)
    ensure_output_dir_exist()
    new_df = embed(new_notes)

    df = pd.concat([df, new_df], axis=1)
    click.echo("Saving df.")
    df.to_csv(DF_FILE)


def embed(notes: dict[(str, str), str]) -> pd.DataFrame:
    # Embeds the notes into openAI and returns a dataframe containing the vectors.
    res = {}
    def showfunc(n): return f"{n[0][0]} {n[0][1]}" if n else ""
    with click.progressbar(notes.items(), item_show_func=showfunc) as note_items:
        for (note, section), text in note_items:
            block = section + ". " + text
            n = num_tokens_from_string(block)
            # Truncate if too long
            if n > EMBEDDING_CTX_LENGTH:
                warnings.warn(
                    f"{note} {section} exceeded token limit. Truncating.")
                block = truncate_text_tokens(block)
            try:
                embedding = get_embedding(DEPLAY_PER_REQUEST, block)
            except Exception as e:
                print(f"Error for {note} {section}", e)
                continue
            res[(note, section)] = embedding
    df = pd.DataFrame(res)
    return df


def query_embeddings(qstr: str, df_file=DF_FILE) -> pd.Series:
    # Given a query string, compare against the embedded notes
    # and return them in order of similarity.
    try:
        df = read_df_file(df_file)
    except FileNotFoundError:
        raise click.ClickException(
            "Could not find database, please run with --build flag"
        )
        return

    # Make cache if it doesn't exist
    try:
        cache = pickle.load(open(CACHE_FILE, "rb"))
    except (OSError, IOError):
        cache = {}

    # Return from cache if it's there else hit API.
    if qstr in cache:
        qvec = cache[qstr]
    else:
        qvec = get_embedding(DEPLAY_PER_REQUEST, qstr)
        cache[qstr] = qvec
        with open(CACHE_FILE, "wb") as f:
            pickle.dump(cache, f)

    # Return notes sorted by similarity
    cos_sim = np.apply_along_axis(
        lambda x: cosine_similarity(x, qvec), axis=0, arr=df)
    results = pd.Series(cos_sim, index=df.columns).sort_values(ascending=False)
    return results


def find_near_unconnected():
    # Based on the embedding vectors, find notes that are near each other but not connected.
    # These are prime candidates for linkage.
    pass


def split_by_filename(filepath: str) -> list[str, str]:
    split_list = filepath.rsplit("/", 1)
    if len(split_list) > 1:
        return split_list[0], split_list[1]

    return filepath, split_list[0]


def present_results(results: pd.Series) -> str:
    # Format the results into a nice table
    resdf = results.reset_index()
    resdf.columns = ["Note", "Section", "Similarity"]
    resdf[["Folder", "Note"]] = resdf["Note"].apply(
        split_by_filename).apply(pd.Series)
    note_filled = resdf["Note"].fillna(resdf["Folder"])
    resdf["Folder"] = np.where(
        resdf["Note"].isnull(), "Atoms", resdf["Folder"])
    resdf["Note"] = note_filled
    resdf = resdf[["Folder", "Note", "Section", "Similarity"]]
    resdf["Note"] = resdf["Note"].str.slice(0, -3)
    resdf["Similarity"] = resdf["Similarity"].round(3)
    resdf["Folder"] = resdf["Folder"].str.slice(0, -1)
    resdf = resdf.rename({"Folder": "Type"}, axis=1)
    resdf = resdf.rename_axis("id", axis=0)

    return tabulate(resdf, headers="keys", tablefmt="psql")


#######
# CLI #
#######

@click.command()
@click.argument("query", required=False)
@click.option("--n", default=10, help="Number of responses to put in ")
@click.option("--build", is_flag=True, help="Recomputes all the embeddings.")
@click.option("--update", is_flag=True, help="Computes embeddings for new notes.")
def cli(query, build, update, n):
    """Query Molecular Notes using OpenAI semantic search."""
    if build:
        click.echo("Building embeddings...")
        build_embeddings()
    elif update:
        click.echo("Updating embedings...")
        update_embeddings()
    if query:
        results = query_embeddings(query)
        results_sub = results.iloc[:n]

        click.echo(present_results(results_sub))
        click.echo()

        idx_in = "1"
        idx_options = [str(x) for x in range(n)]
        idx_options.append("r")

        print("you input a number key to print out the note, or you can use `r` to print out the table again. other key will exit.")
        while idx_in in idx_options:
            click.secho("ENTER INDEX:", bold=True, fg="magenta", nl=False)
            idx_in = click.prompt("", prompt_suffix="")

            if idx_in not in idx_options:
                return

            if idx_in == "q":
                break

            if idx_in == "r":
                click.echo(present_results(results_sub))
                click.echo()
                continue

            note = results_sub.index[int(idx_in)]
            print(note[0])
            with open(f"/{note[0]}", 'r') as file:
                contents = file.read()
            print(contents)

    else:
        click.echo("No query provided.")


if __name__ == "__main__":
    # nmr --build
    # nmr --update
    # nmr "Weaknesses of OLS regression"
    cli()
    # note_path = "/Users/ianzhang/pnotes/pages/5g-jarvis.md"

    # notes = {}
    # sections = extract_sections(note_path)
    # for section_id, section_contents in sections.items():
    #     cleaned_txt = clean_section(section_contents)
    #     if cleaned_txt == "":
    #         continue
    #     notes[(note_path, section_id)] = cleaned_txt

    # for k, v in notes.items():
    #     print("key: {} >> value: {}".format(k, v))
    # read_markdown_notes(NOTES_ROOT_DIR)
