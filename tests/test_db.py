import pyspark
import logging
import requests
import pandas as pd

from mozaggregator.aggregator import _aggregate_metrics, scalar_histogram_labels
from mozaggregator.db import create_connection, submit_aggregates
from dataset import *
from moztelemetry.histogram import Histogram
from nose.tools import nottest


SERVICE_URI = "http://localhost:5000"


def setup_module():
    global aggregates
    global sc

    logger = logging.getLogger("py4j")
    logger.setLevel(logging.ERROR)

    sc = pyspark.SparkContext(master="local[*]")
    raw_pings = list(generate_pings())
    aggregates = _aggregate_metrics(sc.parallelize(raw_pings))


def teardown_module():
    sc.stop()


def test_connection():
    db = create_connection()
    assert(db)


def test_submit():
    # Multiple submissions should not alter the aggregates in the db
    submit_aggregates(aggregates)
    build_id_count, submission_date_count = submit_aggregates(aggregates)

    n_submission_dates = len(ping_dimensions["submission_date"])
    n_channels = len(ping_dimensions["channel"])
    n_versions = len(ping_dimensions["version"])
    n_build_ids = len(ping_dimensions["build_id"])
    assert(build_id_count == n_submission_dates*n_channels*n_versions*n_build_ids)
    assert(submission_date_count == n_submission_dates*n_channels*n_versions)


def test_channels():
    channels = requests.get("{}/aggregates_by/build_id/channels/".format(SERVICE_URI)).json()
    assert(set(channels) == set(ping_dimensions["channel"]))

    channels = requests.get("{}/aggregates_by/submission_date/channels/".format(SERVICE_URI)).json()
    assert(set(channels) == set(ping_dimensions["channel"]))


def test_build_ids():
    template_channel = ping_dimensions["channel"]
    template_version = ping_dimensions["version"]
    template_build_id = ping_dimensions["build_id"]

    for channel in template_channel:
        buildids = requests.get("{}/aggregates_by/build_id/channels/{}/dates/".format(SERVICE_URI, channel)).json()
        assert(len(buildids) == len(template_version)*len(template_build_id))

        for buildid in buildids:
            assert(set(buildid.keys()) == set(["date", "version"]))
            assert(buildid["date"] in [x[:-6] for x in template_build_id])
            assert(buildid["version"] in [x.split('.')[0] for x in template_version])


def test_submission_dates():
    template_channel = ping_dimensions["channel"]
    template_version = ping_dimensions["version"]
    template_submission_date = ping_dimensions["submission_date"]

    for channel in template_channel:
        submission_dates = requests.get("{}/aggregates_by/submission_date/channels/{}/dates/".format(SERVICE_URI, channel)).json()
        assert(len(submission_dates) == len(template_version)*len(template_submission_date))

        for submission_date in submission_dates:
            assert(set(submission_date.keys()) == set(["date", "version"]))
            assert(submission_date["date"] in template_submission_date)
            assert(submission_date["version"] in [x.split('.')[0] for x in template_version])


def test_build_id_metrics():
    template_channel = ping_dimensions["channel"]
    template_version = [x.split('.')[0] for x in ping_dimensions["version"]]
    template_build_id = [x[:-6] for x in ping_dimensions["build_id"]]

    expected_count = NUM_PINGS_PER_DIMENSIONS
    for dimension, values in ping_dimensions.iteritems():
        if dimension not in ["channel", "version", "build_id"]:
            expected_count *= len(values)

    for channel in template_channel:
        for version in template_version:
            for buildid in template_build_id:
                for metric, value in histograms_template.iteritems():
                    test_histogram("build_id", channel, version, buildid, metric, value, expected_count)

                for simple_measure, value in simple_measurements_template.iteritems():
                    if not isinstance(value, int):
                        continue

                    metric = "SIMPLE_MEASURES_{}".format(simple_measure.upper())
                    test_simple_measure("build_id", channel, version, buildid, metric, value, expected_count)

                for metric, histograms in keyed_histograms_template.iteritems():
                    test_keyed_histogram("build_id", channel, version, buildid, metric, histograms, expected_count)


def test_submission_dates_metrics():
    template_channel = ping_dimensions["channel"]
    template_version = [x.split('.')[0] for x in ping_dimensions["version"]]
    template_submission_date = ping_dimensions["submission_date"]

    expected_count = NUM_PINGS_PER_DIMENSIONS
    for dimension, values in ping_dimensions.iteritems():
        if dimension not in ["channel", "version", "submission_date"]:
            expected_count *= len(values)

    for channel in template_channel:
        for version in template_version:
            for submission_date in template_submission_date:
                for metric, value in histograms_template.iteritems():
                    test_histogram("submission_date", channel, version, submission_date, metric, value, expected_count)

                for simple_measure, value in simple_measurements_template.iteritems():
                    if not isinstance(value, int):
                        continue

                    metric = "SIMPLE_MEASURES_{}".format(simple_measure.upper())
                    test_simple_measure("submission_date", channel, version, submission_date, metric, value, expected_count)

                for metric, histograms in keyed_histograms_template.iteritems():
                    test_keyed_histogram("submission_date", channel, version, submission_date, metric, histograms, expected_count)


@nottest
def test_histogram(prefix, channel, version, date, metric, value, expected_count):
    res = requests.get("{}/aggregates_by/{}/channels/{}/dates/{}_{}?metric={}".format(SERVICE_URI, prefix, channel, version, date, metric)).json()
    assert(len(res) == 1)

    res = res[0]
    assert(res["count"] == expected_count*(NUM_CHILDREN_PER_PING + 1))

    if value["histogram_type"] == 4:  # Count histogram
        current = pd.Series({int(k): v for k, v in res["histogram"].iteritems()})
        expected = pd.Series(index=scalar_histogram_labels, data=0)
        expected[SCALAR_BUCKET] = res["count"]
        assert(value["values"]["0"] == SCALAR_VALUE)
        assert(res["histogram"][str(SCALAR_BUCKET)] == res["count"])
        assert((current == expected).all())
    else:
        current = pd.Series({int(k): v for k, v in res["histogram"].iteritems()})
        expected = Histogram(metric, value).get_value()*res["count"]
        assert((current == expected).all())


@nottest
def test_simple_measure(prefix, channel, version, date, metric, value, expected_count):
    res = requests.get("{}/aggregates_by/{}/channels/{}/dates/{}_{}?metric={}".format(SERVICE_URI, prefix, channel, version, date, metric)).json()
    assert(len(res) == 1)

    res = res[0]
    assert(res["count"] == expected_count)

    current = pd.Series({int(k): v for k, v in res["histogram"].iteritems()})
    expected = pd.Series(index=scalar_histogram_labels, data=0)
    expected[SCALAR_BUCKET] = res["count"]
    assert(value == SCALAR_VALUE)
    assert(res["histogram"][str(SCALAR_BUCKET)] == res["count"])
    assert((current == expected).all())


@nottest
def test_keyed_histogram(prefix, channel, version, date, metric, histograms, expected_count):
    res = requests.get("{}/aggregates_by/{}/channels/{}/dates/{}_{}?metric={}".format(SERVICE_URI, prefix, channel, version, date, metric)).json()
    assert(len(res) == len(histograms))

    for label, value in histograms.iteritems():
        res = requests.get("{}/aggregates_by/{}/channels/{}/dates/{}_{}?metric={}&label={}".format(SERVICE_URI, prefix, channel, version, date, metric, label)).json()
        assert(len(res) == 1)

        res = res[0]
        assert(res["count"] == expected_count*(NUM_CHILDREN_PER_PING + 1))

        current = pd.Series({int(k): v for k, v in res["histogram"].iteritems()})
        expected = Histogram(metric, value).get_value()*res["count"]
        assert((current == expected).all())