"""ReplayFilter: a retried stream must not repeat text the caller already has."""

from kohakuterrarium.llm.stream_replay import ReplayFilter


def run(filter_, attempts):
    """Feed each attempt's chunks and return what the caller receives."""
    out = []
    for chunks in attempts:
        filter_.begin_attempt()
        for chunk in chunks:
            out.append(filter_.feed(chunk))
    return "".join(out)


class TestReplayFilter:
    def test_first_attempt_passes_through_untouched(self):
        assert run(ReplayFilter(), [["Hel", "lo"]]) == "Hello"

    def test_retry_that_repeats_the_delivered_text_only_adds_the_rest(self):
        assert run(ReplayFilter(), [["Hel"], ["Hel", "lo", " world"]]) == "Hello world"

    def test_chunk_boundaries_of_the_retry_may_differ(self):
        assert (
            run(ReplayFilter(), [["Hello "], ["He", "llo wor", "ld"]]) == "Hello world"
        )

    def test_a_chunk_that_straddles_the_end_of_the_delivered_text_is_cut(self):
        assert run(ReplayFilter(), [["Hel"], ["Hello"]]) == "Hello"

    def test_retry_that_diverges_is_passed_on_in_full(self):
        received = run(ReplayFilter(), [["The answer is 4"], ["The result equals 4"]])
        assert received == "The answer is 4The result equals 4"

    def test_divergence_in_a_later_chunk_stops_the_suppression_there(self):
        received = run(ReplayFilter(), [["Hello world"], ["Hello ", "there"]])
        assert received == "Hello worldthere"

    def test_retry_that_ends_inside_the_delivered_text_adds_nothing(self):
        assert run(ReplayFilter(), [["Hello world"], ["Hello"]]) == "Hello world"

    def test_third_attempt_compares_against_everything_delivered_so_far(self):
        assert (
            run(ReplayFilter(), [["He"], ["Hello"], ["Hello wor", "ld"]])
            == "Hello world"
        )

    def test_empty_chunks_are_ignored(self):
        assert run(ReplayFilter(), [["Hel", ""], ["", "Hel", "lo"]]) == "Hello"
