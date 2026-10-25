#!/usr/bin/env python3
"""Three-word session names (research D22, FR-037).

make_name() joins a mood, a color and an animal with "-". The lists are fixed: 64 words
each, lower-case ASCII letters only, no duplicates. 64**3 = 262,144 names. A three-word
name has two hyphens, so it can never match rookery_identity._ID_LOOKALIKE
(ag-<10 hex> or rs-<8 hex>).
"""
import secrets

MOODS = (
    "calm", "brave", "bright", "bold", "busy", "clever", "cosmic", "crisp", "eager", "early",
    "fancy", "fond", "gentle", "glad", "grand", "happy", "hardy", "jolly", "keen", "kind",
    "lively", "lucky", "merry", "mighty", "modest", "neat", "noble", "open", "patient",
    "plucky", "polite", "proud", "quick", "quiet", "ready", "royal", "sharp", "shiny", "silent",
    "smart", "snug", "solid", "steady", "sunny", "swift", "tidy", "tender", "trusty", "vivid",
    "warm", "witty", "zesty", "agile", "amber", "deft", "even", "fair", "fresh", "humble",
    "jaunty", "nimble", "perky", "serene", "spry",
)

COLORS = (
    "red", "blue", "green", "gold", "silver", "amber", "azure", "beige", "black", "bronze",
    "brown", "coral", "cream", "crimson", "cyan", "denim", "ebony", "emerald", "fuchsia",
    "golden", "gray", "indigo", "ivory", "jade", "khaki", "lemon", "lilac", "lime", "maroon",
    "mauve", "mint", "navy", "ochre", "olive", "orange", "peach", "pearl", "pink", "plum",
    "purple", "rose", "ruby", "rust", "sage", "salmon", "sand", "scarlet", "sepia", "sienna",
    "slate", "tan", "teal", "topaz", "violet", "white", "yellow", "aqua", "copper", "hazel",
    "linen", "mocha", "onyx", "opal", "pine",
)

ANIMALS = (
    "otter", "fox", "owl", "hare", "wren", "heron", "seal", "lynx", "badger", "beaver", "bison",
    "crane", "deer", "dove", "eagle", "falcon", "finch", "gecko", "goose", "hawk", "ibis",
    "jay", "koala", "lark", "lemur", "llama", "mole", "moose", "newt", "orca", "panda",
    "parrot", "pony", "puffin", "quail", "raven", "robin", "sheep", "sloth", "sparrow", "stork",
    "swan", "tiger", "toad", "trout", "turtle", "vole", "whale", "wolf", "yak", "zebra",
    "camel", "cat", "cow", "duck", "frog", "gull", "horse", "mouse", "pig", "ram", "goat",
    "mule", "lamb",
)


def make_name(rng=None) -> str:
    """Return a name like "calm-blue-otter". rng needs a choice(seq) method."""
    rng = rng or secrets  # looked up at call time, so a test can swap it
    return "-".join(rng.choice(words) for words in (MOODS, COLORS, ANIMALS))
