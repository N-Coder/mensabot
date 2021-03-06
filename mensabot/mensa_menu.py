import datetime as dtm
import difflib
import itertools
import re
import warnings
from collections import Counter
from typing import List, NamedTuple, Optional

import regex as re

PATTERN_TAG = "([AH]?[A-Z]|MV|VG|1?[0-9])"
PATTERN_ZUSATZ = PATTERN_TAG + "(" + PATTERN_TAG + "[,/]\s*)*"
PATTERN_KENNZ = "\s*[,*]\s*(" + PATTERN_TAG + "(" + PATTERN_TAG + "[,/]\s*)*)$"

dish = NamedTuple("dish", [
    ("datum", dtm.datetime),
    ("name", str),
    ("warengruppe", str),
    ("kennz", Counter),
    ("zusatz", Counter),
    ("stud", float),
    ("bed", float),
    ("gast", float)
])


def parse_dish(row: dict) -> dish:
    """
    Parse a row from the csv file as dish, trying to extract further information from the name.
    """

    row['datum'] = dtm.datetime.strptime(row['datum'], "%d.%m.%Y").date()
    row['zusatz'] = Counter()
    row['kennz'] = Counter(row['kennz'].split(",") if row['kennz'] else [])
    row["stud"] = float(row["stud"].replace(",", "."))
    row["bed"] = float(row["bed"].replace(",", "."))
    row["gast"] = float(row["gast"].replace(",", "."))
    del row['tag']
    del row['preis']

    # fix for fucked up Salatmixes
    if row["name"].startswith("Salatmix"):
        row["name"] = re.match("Salatmix( [IV]+)?", row["name"]).group()
    else:
        names, zusatz, kennz = __parse_name(row["name"])
        row["name"] = " ".join(names)
        row['zusatz'] += Counter(zusatz)
        del row['zusatz']['']
        row['kennz'] += Counter(kennz)
        del row['kennz']['']

    return dish(**row)


def __parse_name(str_in):
    str_in = re.sub("\(+", "(", re.sub("\)+", ")", str_in))

    name = []
    kennz = []
    zusatz = []

    def append_name(token):
        nonlocal kennz
        token = token.strip()
        m = re.search(PATTERN_KENNZ, token)
        if m:
            kennz += (s.strip() for s in m.group(1).split(","))
            token = re.sub(PATTERN_KENNZ, "", token)
        name.append(token)

    def append_zusatz(token):
        nonlocal zusatz
        token = token.strip()
        if re.match(PATTERN_ZUSATZ, token):
            zusatz += (s.strip() for s in token.split(","))
        else:
            name.append("(" + token + ")")

    brackets = False
    token = ""
    for c in str_in:
        if c == "(":
            if brackets:
                return str_in, ["??"], ["??"]
            append_name(token)
            token = ""
            brackets = True
        elif c == ")":
            if not brackets:
                return str_in, ["??"], ["??"]
            append_zusatz(token)
            token = ""
            brackets = False
        else:
            token += c

    if token:
        append_name(token)

    return name, zusatz, kennz


########################################################################################################################

class Change(object):
    def __init__(self, type: str, from_dish: Optional[dish], to_dish: Optional[dish]):
        assert from_dish != to_dish
        self.type = type  # type: str
        self.from_dish = from_dish  # type: Optional[dish]
        self.to_dish = to_dish  # type: Optional[dish]

        if not self.from_dish or not self.to_dish:
            self.diff = {}
        else:
            self.diff = {
                attr: (self.from_dish[idx], self.to_dish[idx])
                for idx, attr in enumerate(dish._fields)
                if self.from_dish[idx] != self.to_dish[idx]
            }

        if self.type == "MOVE":
            if from_dish.name == to_dish.name and from_dish.warengruppe == to_dish.warengruppe:
                self.type = "ATTR"
                assert self.diff
            else:
                assert from_dish.warengruppe != to_dish.warengruppe

    def dish(self) -> dish:
        return self.to_dish if self.to_dish else self.from_dish

    def __eq__(self, other):
        return isinstance(other, Change) and \
               self.type == other.type and \
               self.from_dish == other.from_dish and \
               self.to_dish == other.to_dish

    def __str__(self):
        if self.type == "ATTR":
            return "%s ATR: %s (%s)" % (
                self.from_dish.datum, self.from_dish.warengruppe, self.from_dish.name
            )
        elif self.type == "MOVE":
            return "%s MOV: %s -> %s (%s)" % (
                self.from_dish.datum, self.from_dish.warengruppe, self.to_dish.warengruppe, self.from_dish.name
            )
        elif self.type == "RENAME":
            return "%s REN: %s -> %s (%s -> %s)" % (
                self.from_dish.datum, self.from_dish.name, self.to_dish.name, self.from_dish.warengruppe,
                self.to_dish.warengruppe
            )
        elif self.type == "REPLACE":
            return "%s REP: %s -> %s (%s)" % (
                self.from_dish.datum, self.from_dish.name, self.to_dish.name, self.from_dish.warengruppe
            )
        elif self.type == "REMOVE":
            return "%s REM: - %s (%s)" % (
                self.from_dish.datum, self.from_dish.warengruppe, self.from_dish.name
            )
        elif self.type == "ADD":
            return "%s ADD: + %s (%s)" % (
                self.to_dish.datum, self.to_dish.warengruppe, self.to_dish.name
            )


def generate_diff(menu1, menu2) -> List[Change]:
    if menu1 == menu2:
        return []

    dates = {dish.datum for dish in menu1}
    map1 = {datum: {dish.warengruppe: dish for dish in menu1 if dish.datum == datum} for datum in dates}
    map2 = {datum: {dish.warengruppe: dish for dish in menu2 if dish.datum == datum} for datum in dates}

    diff = []
    for date in dates:
        if map1[date] == map2[date]:
            continue

        # find all warengruppen that contain changes
        changed = {
            re.sub("[0-9]", "", wg) for wg
            in itertools.chain(map1[date].keys(), map2[date].keys())
            if map1[date].get(wg, None) != map2[date].get(wg, None)
        }

        for wg in changed:
            # compare all items in the concerned warengruppe
            changed_wg1 = [dish for dish in map1[date].values() if dish.warengruppe.startswith(wg)]
            changed_wg2 = [dish for dish in map2[date].values() if dish.warengruppe.startswith(wg)]
            changed_wg1.sort(key=lambda x: x.warengruppe)  # sort items to make algorithm deterministic
            changed_wg2.sort(key=lambda x: x.warengruppe)
            diff += __compare_changed_wg(changed_wg1, changed_wg2)

    return diff


def __compare_changed_wg(changed_wg1, changed_wg2):
    diff, removed = [], []

    names1 = {d.name: d for d in changed_wg1}
    names2 = {d.name: d for d in changed_wg2}

    for dish in changed_wg1:
        try:
            if dish == names2[dish.name]:
                continue
            diff.append(Change("MOVE", dish, names2[dish.name]))
        except KeyError:
            matches = difflib.get_close_matches(dish.name, names2)
            if len(matches) == 1:  # TODO what if len > 1?
                diff.append(Change("RENAME", dish, names2[matches[0]]))
            else:
                removed.append(Change("REMOVE", dish, None))

    for dish in changed_wg2:
        try:
            if dish == names1[dish.name]:
                continue
            assert Change("MOVE", names1[dish.name], dish) in diff  # TODO better handling of this case
        except KeyError:
            matches = difflib.get_close_matches(dish.name, names1)
            if len(matches) == 1:
                if not Change("RENAME", names1[matches[0]], dish) in diff:
                    warnings.warn("Dish %s renamed to %s, but only found in one direction. Diff will be invalid!" %
                                  (dish, matches[0]))  # TODO better handling of this case
            elif removed:
                diff.append(Change("REPLACE", removed.pop(0).from_dish, dish))
            else:
                diff.append(Change("ADD", None, dish))

    return diff + removed


########################################################################################################################

def main():
    import argparse
    import csv
    from mensabot.mensa import PRICES_CATEGORIES, MENU_TYPES

    parser = argparse.ArgumentParser(description='Compare two mensa menus.')
    parser.add_argument('path', action='store')
    parser.add_argument('old_file', action='store')
    parser.add_argument('old_hex', action='store')
    parser.add_argument('old_mode', action='store')
    parser.add_argument('new_file', action='store')
    parser.add_argument('new_hex', action='store')
    parser.add_argument('new_mode', action='store')
    args = parser.parse_args()

    with open(args.old_file, "r", encoding="iso8859_3") as f:
        menu1 = [parse_dish(row) for row in csv.DictReader(f.readlines(), delimiter=';')]
    with open(args.new_file, "r", encoding="iso8859_3") as f:
        menu2 = [parse_dish(row) for row in csv.DictReader(f.readlines(), delimiter=';')]

    diff = generate_diff(menu1, menu2)
    diff = sorted(diff, key=lambda d: (d.dish().datum, MENU_TYPES.index(d.dish().warengruppe[0]), d.dish().warengruppe))
    for x in diff:
        if list(x.diff.keys()) == ["warengruppe"]:
            continue

        weekday = x.dish().datum.strftime("%a")
        if "name" in x.diff:
            print("[%s ~] %s ->️ %s" % ((weekday,) + x.diff["name"]))
        elif not x.from_dish:
            print("[%s +] %s" % (weekday, x.to_dish.name))
        elif not x.to_dish:
            print("[%s -] %s" % (weekday, x.from_dish.name))
        else:
            print("[%s ~] %s" % (weekday, x.from_dish.name))

        if any(s in x.diff for s in PRICES_CATEGORIES):
            print("\tPreis: (%s/%s/%s) ->️ (%s/%s/%s)" %
                  (x.from_dish.stud, x.from_dish.bed, x.from_dish.gast,
                   x.to_dish.stud, x.to_dish.bed, x.to_dish.gast))
        if "kennz" in x.diff:
            print("\tKennz: %s -> %s" % tuple(",".join(c.keys()) for c in x.diff["kennz"]))
        if "zusatz" in x.diff:
            print("\tZusatz: %s ->️ %s" % tuple(",".join(c.keys()) for c in x.diff["zusatz"]))


if __name__ == "__main__":
    main()
