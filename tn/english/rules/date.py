# Copyright (c) 2021, NVIDIA CORPORATION.  All rights reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import pynini
from pynini.examples import plurals
from pynini.lib import pynutil

from tn.processor import Processor
from tn.utils import get_abs_path, load_labels, augment_labels_with_punct_at_end
from tn.english.rules.cardinal import Cardinal
from tn.english.rules.ordinal import Ordinal

graph_teen = pynini.invert(
    pynini.string_file(
        get_abs_path("english/data/number/teen.tsv"))).optimize()
graph_digit = pynini.invert(
    pynini.string_file(
        get_abs_path("english/data/number/digit.tsv"))).optimize()
ties_graph = pynini.invert(
    pynini.string_file(get_abs_path("english/data/number/ty.tsv"))).optimize()
year_suffix = load_labels(get_abs_path("english/data/date/year_suffix.tsv"))
year_suffix.extend(augment_labels_with_punct_at_end(year_suffix))
year_suffix = pynini.string_map(year_suffix).optimize()


def get_ties_graph(deterministic: bool = False):
    """
    Returns two digit transducer, e.g.
    03 -> o three
    12 -> thirteen
    20 -> twenty
    """
    graph = graph_teen | ties_graph + pynutil.delete(
        "0") | ties_graph + Processor("tmp").INSERT_SPACE + graph_digit

    if deterministic:
        graph = graph | pynini.cross(
            "0", "oh") + Processor("tmp").INSERT_SPACE + graph_digit
    else:
        graph = graph | (pynini.cross("0", "oh") | pynini.cross(
            "0", "zero")) + Processor("tmp").INSERT_SPACE + graph_digit

    return graph.optimize()


def get_four_digit_year_graph(deterministic: bool = False):
    """
    Returns a four digit transducer which is combination of ties/teen or digits
    (using hundred instead of thousand format), e.g.
    1219 -> twelve nineteen
    3900 -> thirty nine hundred
    """
    graph_ties = get_ties_graph(deterministic)

    graph_with_s = (
        (graph_ties + Processor("tmp").INSERT_SPACE + graph_ties)
        | (graph_teen + Processor("tmp").INSERT_SPACE +
           (ties_graph | pynini.cross("1", "ten")))) + pynutil.delete("0s")

    graph_with_s |= (graph_teen | graph_ties
                     ) + Processor("tmp").INSERT_SPACE + pynini.cross(
                         "00", "hundred") + pynutil.delete("s")
    graph_with_s = graph_with_s @ pynini.cdrewrite(
        pynini.cross("y", "ies") | pynutil.insert("s"), "", "[EOS]",
        pynini.closure(Processor("tmp").VCHAR))

    graph = graph_ties + Processor("tmp").INSERT_SPACE + graph_ties
    graph |= (graph_teen
              | graph_ties) + Processor("tmp").INSERT_SPACE + pynini.cross(
                  "00", "hundred")

    thousand_graph = (
        graph_digit + Processor("tmp").INSERT_SPACE +
        pynini.cross("00", "thousand") +
        (pynutil.delete("0") | Processor("tmp").INSERT_SPACE + graph_digit))
    thousand_graph |= (graph_digit + Processor("tmp").INSERT_SPACE +
                       pynini.cross("000", "thousand") +
                       pynini.closure(pynutil.delete(" "), 0, 1) +
                       pynini.accep("s"))

    graph |= graph_with_s
    if deterministic:
        graph = plurals._priority_union(thousand_graph, graph,
                                        pynini.closure(Processor("tmp").VCHAR))
    else:
        graph |= thousand_graph

    return graph.optimize()


def _get_two_digit_year_with_s_graph():
    # to handle '70s -> seventies
    graph = (pynini.closure(pynutil.delete("'"), 0, 1) + pynini.compose(
        ties_graph + pynutil.delete("0s"),
        pynini.cdrewrite(pynini.cross("y", "ies"), "", "[EOS]",
                         pynini.closure(Processor("tmp").VCHAR)))).optimize()
    return graph


def _get_year_graph(cardinal_graph, deterministic: bool = False):
    """
    Transducer for year, only from 1000 - 2999 e.g.
    1290 -> twelve nineteen
    2000 - 2009 will be verbalized as two thousand.

    Transducer for 3 digit year, e.g. 123-> one twenty three

    Transducer for year with suffix
    123 A.D., 4200 B.C
    """
    graph = get_four_digit_year_graph(deterministic)
    graph = (pynini.union("1", "2") + (Processor("tmp").DIGIT**3) +
             pynini.closure(pynini.cross(" s", "s") | "s", 0, 1)) @ graph

    graph |= _get_two_digit_year_with_s_graph()

    three_digit_year = (Processor("tmp").DIGIT @ cardinal_graph) + Processor(
        "tmp").INSERT_SPACE + (Processor("tmp").DIGIT**2) @ cardinal_graph
    year_with_suffix = (
        (get_four_digit_year_graph(deterministic=True) | three_digit_year) +
        Processor("tmp").DELETE_SPACE + Processor("tmp").INSERT_SPACE +
        year_suffix)
    graph |= year_with_suffix
    return graph.optimize()


def _get_two_digit_year(cardinal_graph, single_digits_graph):
    two_digit_year = Processor("tmp").DIGIT**(2) @ plurals._priority_union(
        cardinal_graph, single_digits_graph,
        pynini.closure(Processor("tmp").VCHAR))
    return two_digit_year


def _get_financial_period_graph():
    # 1H23 -> first half of twenty three
    # 3Q22 -> third quarter of twenty two

    h_ordinals = pynini.cross('1', 'first') | pynini.cross('2', 'second')
    q_ordinals = h_ordinals | pynini.cross('3', 'third') | pynini.cross(
        '4', 'fourth')

    h_graph = h_ordinals + pynini.cross('H', ' half')
    q_graph = q_ordinals + pynini.cross('Q', ' quarter')
    period_graph = h_graph | q_graph

    return period_graph


class Date(Processor):

    def __init__(self, deterministic: bool = False):
        """
        Args:
            deterministic: if True will provide a single transduction option,
                for False multiple transduction are generated (used for audio-based normalization)
        """
        super().__init__("date", ordertype="en_tn")
        self.deterministic = deterministic
        self.build_tagger()
        self.build_verbalizer()

    def build_tagger(self):
        """
        Finite state transducer for classifying date, e.g.
            jan. 5, 2012 -> date { month: "january" day: "five" year: ", twenty twelve" preserve_order: "true" }
            jan. 5 -> date { month: "january" day: "five" preserve_order: "true" }
            5 january 2012 -> date { day: "five" month: "january" year: "twenty twelve" preserve_order: "true" }
            2012-01-05 -> date { year: "twenty twelve" month: "january" day: "five" }
            2012.01.05 -> date { year: "twenty twelve" month: "january" day: "five" }
            2012/01/05 -> date { year: "twenty twelve" month: "january" day: "five" }
            2012 -> date { year: "twenty twelve" }
        """
        cardinal = Cardinal(self.deterministic)
        # january
        month_graph = pynini.string_file(
            get_abs_path("english/data/date/month_name.tsv")).optimize()
        # January, JANUARY
        month_graph |= pynini.compose(
            self.TO_LOWER + pynini.closure(self.VCHAR),
            month_graph) | pynini.compose(self.TO_LOWER**(2, ...), month_graph)

        # jan
        month_abbr_graph = pynini.string_file(
            get_abs_path("english/data/date/month_abbr.tsv")).optimize()
        # jan, Jan, JAN
        month_abbr_graph = (
            month_abbr_graph
            | pynini.compose(self.TO_LOWER + pynini.closure(self.LOWER, 1),
                             month_abbr_graph).optimize()
            | pynini.compose(self.TO_LOWER**(2, ...),
                             month_abbr_graph).optimize()) + pynini.closure(
                                 pynutil.delete("."), 0, 1)
        month_graph |= month_abbr_graph.optimize()

        month_numbers_labels = pynini.string_file(
            get_abs_path("english/data/date/month_number.tsv")).optimize()
        cardinal_graph = cardinal.graph_hundred_component_at_least_one_none_zero_digit

        year_graph = _get_year_graph(cardinal_graph=cardinal_graph,
                                     deterministic=self.deterministic)

        # three_digit_year = (self.DIGIT @ cardinal_graph) + self.INSERT_SPACE + (self.DIGIT ** 2) @ cardinal_graph
        # year_graph |= three_digit_year

        month_graph = pynutil.insert(
            "month: \"") + month_graph + pynutil.insert("\"")
        month_numbers_graph = pynutil.insert(
            "month: \"") + month_numbers_labels + pynutil.insert("\"")

        endings = ["rd", "th", "st", "nd"]
        endings += [x.upper() for x in endings]
        endings = pynini.union(*endings)

        day_graph = (
            pynutil.insert("day: \"") +
            pynini.closure(pynutil.delete("the "), 0, 1) +
            (((pynini.union("1", "2") + self.DIGIT) | self.DIGIT |
              (pynini.accep("3") + pynini.union("0", "1"))) +
             pynini.closure(pynutil.delete(endings), 0, 1)) @ cardinal_graph +
            pynutil.insert("\""))

        two_digit_year = _get_two_digit_year(
            cardinal_graph=cardinal_graph,
            single_digits_graph=cardinal.single_digits_graph)
        two_digit_year = pynutil.insert(
            "year: \"") + two_digit_year + pynutil.insert("\"")

        graph_year = pynutil.insert(" year: \"") + pynutil.delete(
            " ") + year_graph + pynutil.insert("\"")
        graph_year |= (pynutil.insert(" year: \"") + pynini.accep(",") +
                       pynini.closure(pynini.accep(" "), 0, 1) + year_graph +
                       pynutil.insert("\""))
        optional_graph_year = pynini.closure(graph_year, 0, 1)

        year_graph = pynutil.insert("year: \"") + year_graph + pynutil.insert(
            "\"")

        graph_mdy = month_graph + (
            (self.DELETE_EXTRA_SPACE + day_graph)
            | (pynini.accep(" ") + day_graph)
            | graph_year
            | (self.DELETE_EXTRA_SPACE + day_graph + graph_year))

        graph_mdy |= (month_graph + pynini.cross("-", " ") + day_graph +
                      pynini.closure(
                          ((pynini.cross("-", " ") +
                            pynini.closure(self.VCHAR)) @ graph_year), 0, 1))

        for x in ["-", "/", "."]:
            delete_sep = pynutil.delete(x)
            graph_mdy |= (month_numbers_graph + delete_sep +
                          self.INSERT_SPACE +
                          pynini.closure(pynutil.delete("0"), 0, 1) +
                          day_graph + delete_sep + self.INSERT_SPACE +
                          (year_graph | two_digit_year))

        graph_dmy = day_graph + self.DELETE_EXTRA_SPACE + month_graph + optional_graph_year
        day_ex_month = (self.DIGIT**2 - pynini.project(month_numbers_graph,
                                                       "input")) @ day_graph
        for x in ["-", "/", "."]:
            delete_sep = pynutil.delete(x)
            graph_dmy |= (day_ex_month + delete_sep + self.INSERT_SPACE +
                          month_numbers_graph + delete_sep +
                          self.INSERT_SPACE + (year_graph | two_digit_year))

        graph_ymd = pynini.accep("")
        for x in ["-", "/", "."]:
            delete_sep = pynutil.delete(x)
            graph_ymd |= ((year_graph | two_digit_year) + delete_sep +
                          self.INSERT_SPACE + month_numbers_graph +
                          delete_sep + self.INSERT_SPACE +
                          pynini.closure(pynutil.delete("0"), 0, 1) +
                          day_graph)

        final_graph = graph_mdy | graph_dmy

        if not self.deterministic:
            final_graph += pynini.closure(
                pynutil.insert(" preserve_order: \"true\""), 0, 1)
            m_sep_d = (month_numbers_graph +
                       pynutil.delete(pynini.union("-", "/")) +
                       self.INSERT_SPACE +
                       pynini.closure(pynutil.delete("0"), 0, 1) + day_graph)
            final_graph |= m_sep_d
        else:
            final_graph += pynutil.insert(" preserve_order: \"true\"")

        period_fy = pynutil.insert(
            "text: \"") + _get_financial_period_graph() + pynutil.insert("\"")
        graph_fy = period_fy + self.INSERT_SPACE + two_digit_year

        final_graph |= year_graph | graph_fy

        ymd_to_mdy_graph = None
        ymd_to_dmy_graph = None
        mdy_to_dmy_graph = None
        md_to_dm_graph = None

        for month in [
                x[0] for x in load_labels(
                    get_abs_path("english/data/date/month_name.tsv"))
        ]:
            for day in [
                    x[0] for x in load_labels(
                        get_abs_path("english/data/date/day.tsv"))
            ]:
                ymd_to_mdy_curr = (pynutil.insert("month: \"" + month +
                                                  "\" day: \"" + day + "\" ") +
                                   pynini.accep('year:') +
                                   pynini.closure(self.VCHAR) +
                                   pynutil.delete(" month: \"" + month +
                                                  "\" day: \"" + day + "\""))

                # YY-MM-DD -> MM-DD-YY
                ymd_to_mdy_curr = pynini.compose(graph_ymd, ymd_to_mdy_curr)
                ymd_to_mdy_graph = (ymd_to_mdy_curr if
                                    ymd_to_mdy_graph is None else pynini.union(
                                        ymd_to_mdy_curr, ymd_to_mdy_graph))

                ymd_to_dmy_curr = (
                    pynutil.insert("day: \"" + day + "\" month: \"" + month +
                                   "\" ") + pynini.accep('year:') +
                    pynini.closure(self.VCHAR) +
                    pynutil.delete(" month: \"" + month + "\" day: \"" + day +
                                   "\""))

                # YY-MM-DD -> MM-DD-YY
                ymd_to_dmy_curr = pynini.compose(graph_ymd,
                                                 ymd_to_dmy_curr).optimize()
                ymd_to_dmy_graph = (ymd_to_dmy_curr if
                                    ymd_to_dmy_graph is None else pynini.union(
                                        ymd_to_dmy_curr, ymd_to_dmy_graph))

                mdy_to_dmy_curr = (
                    pynutil.insert("day: \"" + day + "\" month: \"" + month +
                                   "\" ") +
                    pynutil.delete("month: \"" + month + "\" day: \"" + day +
                                   "\" ") + pynini.accep('year:') +
                    pynini.closure(self.VCHAR)).optimize()
                # MM-DD-YY -> verbalize as MM-DD-YY (February fourth 1991) or DD-MM-YY (the fourth of February 1991)
                mdy_to_dmy_curr = pynini.compose(graph_mdy,
                                                 mdy_to_dmy_curr).optimize()
                mdy_to_dmy_graph = (
                    mdy_to_dmy_curr if mdy_to_dmy_graph is None else
                    pynini.union(mdy_to_dmy_curr,
                                 mdy_to_dmy_graph).optimize()).optimize()

                md_to_dm_curr = pynutil.insert(
                    "day: \"" + day + "\" month: \"" + month +
                    "\"") + pynutil.delete("month: \"" + month + "\" day: \"" +
                                           day + "\"")
                md_to_dm_curr = pynini.compose(m_sep_d,
                                               md_to_dm_curr).optimize()

                md_to_dm_graph = (
                    md_to_dm_curr if md_to_dm_graph is None else pynini.union(
                        md_to_dm_curr, md_to_dm_graph).optimize()).optimize()

        if not self.deterministic:
            final_graph |= mdy_to_dmy_graph | md_to_dm_graph | ymd_to_dmy_graph
        else:
            final_graph |= ymd_to_mdy_graph

        final_graph = self.add_tokens(final_graph)
        self.tagger = final_graph.optimize()

    def build_verbalizer(self):
        """
        Finite state transducer for verbalizing date, e.g.
            date { month: "february" day: "five" year: "twenty twelve" preserve_order: "true" } -> february fifth twenty twelve
            date { day: "five" month: "february" year: "twenty twelve" preserve_order: "true" } -> the fifth of february twenty twelve
        """
        ordinal = Ordinal(self.deterministic)
        phrase = pynini.closure(self.NOT_QUOTE, 1)
        day_cardinal = (pynutil.delete("day:") + self.DELETE_SPACE +
                        pynutil.delete("\"") +
                        pynini.closure(self.NOT_QUOTE, 1) +
                        pynutil.delete("\""))
        day = day_cardinal @ ordinal.suffix
        period = pynutil.delete("text:") + self.DELETE_SPACE + pynutil.delete(
            "\"") + phrase + pynutil.delete("\"")
        month = pynutil.delete("month:") + self.DELETE_SPACE + pynutil.delete(
            "\"") + phrase + pynutil.delete("\"")

        year = (pynutil.delete("year:") + self.DELETE_SPACE +
                pynutil.delete("\"") + pynini.closure(self.NOT_QUOTE, 1) +
                self.DELETE_SPACE + pynutil.delete("\""))

        # financial period
        graph_fy = (pynutil.insert("the ") + period + pynutil.insert(" of") +
                    pynini.closure(self.DELETE_EXTRA_SPACE + year, 0, 1))

        # month (day) year
        graph_mdy = (month +
                     pynini.closure(self.DELETE_EXTRA_SPACE + day, 0, 1) +
                     pynini.closure(self.DELETE_EXTRA_SPACE + year, 0, 1))
        # may 5 -> may five
        if not self.deterministic:
            graph_mdy |= (
                month +
                pynini.closure(self.DELETE_EXTRA_SPACE + day_cardinal, 0, 1) +
                pynini.closure(self.DELETE_EXTRA_SPACE + year, 0, 1))

        # day month year
        graph_dmy = (pynutil.insert("the ") + day + self.DELETE_EXTRA_SPACE +
                     pynutil.insert("of ") + month +
                     pynini.closure(self.DELETE_EXTRA_SPACE + year, 0, 1))

        optional_preserve_order = pynini.closure(
            pynutil.delete("preserve_order:") + self.DELETE_SPACE +
            pynutil.delete("\"true\"") + self.DELETE_SPACE
            | pynutil.delete("field_order:") + self.DELETE_SPACE +
            pynutil.delete("\"") + self.NOT_QUOTE + pynutil.delete("\"") +
            self.DELETE_SPACE)

        final_graph = ((plurals._priority_union(
            graph_mdy, pynutil.add_weight(graph_dmy, 0.0001),
            pynini.closure(self.VCHAR)) | year | graph_fy) +
                       self.DELETE_SPACE + optional_preserve_order)  # noqa
        delete_tokens = self.delete_tokens(final_graph)
        self.verbalizer = delete_tokens.optimize()
