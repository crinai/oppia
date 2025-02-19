# coding: utf-8
#
# Copyright 2014 The Oppia Authors. All Rights Reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS-IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Tests for long running jobs and continuous computations."""

from __future__ import absolute_import  # pylint: disable=import-only-modules
from __future__ import unicode_literals  # pylint: disable=import-only-modules

import ast
import logging
import re

from core import jobs
from core import jobs_registry
from core.domain import event_services
from core.domain import exp_domain
from core.domain import exp_services
from core.domain import taskqueue_services
from core.platform import models
from core.tests import test_utils
import feconf
import python_utils

from mapreduce import input_readers

(base_models, exp_models, stats_models, job_models) = (
    models.Registry.import_models([
        models.NAMES.base_model, models.NAMES.exploration,
        models.NAMES.statistics, models.NAMES.job]))

datastore_services = models.Registry.import_datastore_services()
transaction_services = models.Registry.import_transaction_services()

JOB_FAILED_MESSAGE = 'failed (as expected)'


class MockJobManagerOne(jobs.BaseMapReduceJobManager):
    """Test job that counts the total number of explorations."""

    @classmethod
    def entity_classes_to_map_over(cls):
        return [exp_models.ExplorationModel]

    @staticmethod
    def map(item):
        current_class = MockJobManagerOne
        if current_class.entity_created_before_job_queued(item):
            yield ('sum', 1)

    @staticmethod
    def reduce(key, values):
        yield (key, sum([int(value) for value in values]))


class MockJobManagerTwo(jobs.BaseMapReduceJobManager):
    """Test job that counts the total number of explorations."""

    @classmethod
    def entity_classes_to_map_over(cls):
        return [exp_models.ExplorationModel]

    @staticmethod
    def map(item):
        current_class = MockJobManagerTwo
        if current_class.entity_created_before_job_queued(item):
            yield ('sum', 1)

    @staticmethod
    def reduce(key, values):
        yield (key, sum([int(value) for value in values]))


class MockFailingJobManager(jobs.BaseMapReduceJobManager):

    @classmethod
    def entity_classes_to_map_over(cls):
        return [exp_models.ExplorationModel]

    @staticmethod
    def map(item):
        current_class = MockFailingJobManager
        if current_class.entity_created_before_job_queued(item):
            yield ('sum', 1)

    @staticmethod
    def reduce(key, values):
        yield (key, sum([int(value) for value in values]))


class JobManagerUnitTests(test_utils.GenericTestBase):
    """Test basic job manager operations."""

    def test_create_new(self):
        """Test the creation of a new job."""
        job_id = MockJobManagerOne.create_new()
        self.assertTrue(job_id.startswith('MockJobManagerOne'))
        self.assertEqual(
            MockJobManagerOne.get_status_code(job_id), jobs.STATUS_CODE_NEW)
        self.assertIsNone(MockJobManagerOne.get_time_queued_msec(job_id))
        self.assertIsNone(MockJobManagerOne.get_time_started_msec(job_id))
        self.assertIsNone(MockJobManagerOne.get_time_finished_msec(job_id))
        self.assertIsNone(MockJobManagerOne.get_metadata(job_id))
        self.assertIsNone(MockJobManagerOne.get_output(job_id))
        self.assertIsNone(MockJobManagerOne.get_error(job_id))

        self.assertFalse(MockJobManagerOne.is_active(job_id))
        self.assertFalse(MockJobManagerOne.has_finished(job_id))

    def test_enqueue_job(self):
        """Test the enqueueing of a job."""
        job_id = MockJobManagerOne.create_new()
        MockJobManagerOne.enqueue(job_id, taskqueue_services.QUEUE_NAME_DEFAULT)
        self.assertEqual(
            MockJobManagerOne.get_status_code(job_id), jobs.STATUS_CODE_QUEUED)
        self.assertIsNotNone(MockJobManagerOne.get_time_queued_msec(job_id))
        self.assertIsNone(MockJobManagerOne.get_output(job_id))

    def test_complete_job(self):
        job_id = MockJobManagerOne.create_new()
        MockJobManagerOne.enqueue(job_id, taskqueue_services.QUEUE_NAME_DEFAULT)
        self.assertEqual(
            MockJobManagerOne.get_status_code(job_id),
            jobs.STATUS_CODE_QUEUED)
        self.process_and_flush_pending_mapreduce_tasks()

        self.assertEqual(
            MockJobManagerOne.get_status_code(job_id),
            jobs.STATUS_CODE_COMPLETED)
        time_queued_msec = MockJobManagerOne.get_time_queued_msec(job_id)
        time_started_msec = MockJobManagerOne.get_time_started_msec(job_id)
        time_finished_msec = MockJobManagerOne.get_time_finished_msec(job_id)
        self.assertIsNotNone(time_queued_msec)
        self.assertIsNotNone(time_started_msec)
        self.assertIsNotNone(time_finished_msec)
        self.assertLess(time_queued_msec, time_started_msec)
        self.assertLess(time_started_msec, time_finished_msec)

        output = MockJobManagerOne.get_output(job_id)
        error = MockJobManagerOne.get_error(job_id)
        self.assertEqual(output, [])
        self.assertIsNone(error)

        self.assertFalse(MockJobManagerOne.is_active(job_id))
        self.assertTrue(MockJobManagerOne.has_finished(job_id))

    def test_base_job_manager_enqueue_raises_error(self):
        with self.assertRaisesRegexp(
            NotImplementedError,
            'Subclasses of BaseJobManager should implement _real_enqueue().'):
            jobs.BaseJobManager._real_enqueue(  # pylint: disable=protected-access
                'job_id', taskqueue_services.QUEUE_NAME_DEFAULT, None, None)

    def test_cannot_instantiate_jobs_from_abstract_base_classes(self):
        with self.assertRaisesRegexp(
            Exception, 'directly create a job using the abstract base'
            ):
            jobs.BaseJobManager.create_new()

    def test_compress_output_list_with_single_char_outputs(self):
        job_id = MockJobManagerOne.create_new()
        input_list = [1, 2, 3, 4, 5]
        expected_output = ['1', '2', '3', '<TRUNCATED>']
        MockJobManagerOne.enqueue(job_id, taskqueue_services.QUEUE_NAME_DEFAULT)
        MockJobManagerOne.register_start(job_id)
        MockJobManagerOne.register_completion(
            job_id, input_list, max_output_len_chars=3)
        actual_output = MockJobManagerOne.get_output(job_id)
        self.assertEqual(actual_output, expected_output)

    def test_failure_for_job_enqueued_using_wrong_manager(self):
        job_id = MockJobManagerOne.create_new()
        with self.assertRaisesRegexp(Exception, 'Invalid job type'):
            MockJobManagerTwo.enqueue(
                job_id, taskqueue_services.QUEUE_NAME_DEFAULT)

    def test_cancelling_multiple_unfinished_jobs(self):
        job1_id = MockJobManagerOne.create_new()
        MockJobManagerOne.enqueue(
            job1_id, taskqueue_services.QUEUE_NAME_DEFAULT)
        job2_id = MockJobManagerOne.create_new()
        MockJobManagerOne.enqueue(
            job2_id, taskqueue_services.QUEUE_NAME_DEFAULT)

        MockJobManagerOne.cancel_all_unfinished_jobs('admin_user_id')

        self.assertFalse(MockJobManagerOne.is_active(job1_id))
        self.assertFalse(MockJobManagerOne.is_active(job2_id))
        self.assertEqual(
            MockJobManagerOne.get_status_code(job1_id),
            jobs.STATUS_CODE_CANCELED)
        self.assertEqual(
            MockJobManagerOne.get_status_code(job2_id),
            jobs.STATUS_CODE_CANCELED)
        self.assertIsNone(MockJobManagerOne.get_output(job1_id))
        self.assertIsNone(MockJobManagerOne.get_output(job2_id))
        self.assertEqual(
            'Canceled by admin_user_id', MockJobManagerOne.get_error(job1_id))
        self.assertEqual(
            'Canceled by admin_user_id', MockJobManagerOne.get_error(job2_id))

    def test_status_code_transitions(self):
        """Test that invalid status code transitions are caught."""
        job_id = MockJobManagerOne.create_new()
        MockJobManagerOne.enqueue(job_id, taskqueue_services.QUEUE_NAME_DEFAULT)

        with self.assertRaisesRegexp(Exception, 'Invalid status code change'):
            MockJobManagerOne.register_completion(job_id, ['output'])
        with self.assertRaisesRegexp(Exception, 'Invalid status code change'):
            MockJobManagerOne.register_failure(job_id, 'error')

    def test_failing_jobs(self):
        # Mocks GoogleCloudStorageInputReader() to fail a job.
        _mock_input_reader = lambda _, __: python_utils.divide(1, 0)

        input_reader_swap = self.swap(
            input_readers, 'GoogleCloudStorageInputReader', _mock_input_reader)

        job_id = MockJobManagerOne.create_new()
        store_map_reduce_results = jobs.StoreMapReduceResults()

        with python_utils.ExitStack() as stack:
            captured_logs = stack.enter_context(
                self.capture_logging(min_level=logging.ERROR))
            stack.enter_context(input_reader_swap)
            stack.enter_context(
                self.assertRaisesRegexp(
                    Exception,
                    r'Invalid status code change for job '
                    r'MockJobManagerOne-\w+-\w+: from new to failed'
                )
            )

            store_map_reduce_results.run(
                job_id, 'core.jobs_test.MockJobManagerOne', 'output')

        # The first log message is ignored as it is the traceback.
        self.assertEqual(len(captured_logs), 1)
        self.assertTrue(
            captured_logs[0].startswith('Job %s failed at' % job_id))

    def test_register_failure(self):
        job_id = MockJobManagerOne.create_new()
        MockJobManagerOne.enqueue(job_id, taskqueue_services.QUEUE_NAME_DEFAULT)
        model = job_models.JobModel.get(job_id, strict=True)
        model.status_code = jobs.STATUS_CODE_STARTED
        model.job_type = MockJobManagerOne.__name__
        MockJobManagerOne.register_failure(job_id, 'Error')
        model = job_models.JobModel.get(job_id, strict=True)
        self.assertEqual(model.error, 'Error')
        self.assertEqual(model.status_code, jobs.STATUS_CODE_FAILED)


SUM_MODEL_ID = 'all_data_id'


class MockNumbersModel(datastore_services.Model):
    number = datastore_services.IntegerProperty()


class MockSumModel(datastore_services.Model):
    total = datastore_services.IntegerProperty(default=0)
    failed = datastore_services.BooleanProperty(default=False)


class FailingAdditionJobManager(jobs.BaseMapReduceJobManager):
    """Test job that stores stuff in MockSumModel and then fails."""

    @classmethod
    def _post_failure_hook(cls, job_id):
        model = MockSumModel.get_by_id(SUM_MODEL_ID)
        model.failed = True
        model.update_timestamps()
        model.put()


class DatastoreJobIntegrationTests(test_utils.GenericTestBase):
    """Tests the behavior of a job that affects data in the datastore.

    This job gets all MockNumbersModel instances and sums their values, and puts
    the summed values in a MockSumModel instance with id SUM_MODEL_ID. The
    computation is redone from scratch each time the job is run.
    """

    def _get_stored_total(self):
        """Returns the total summed values of all the MockNumbersModel instances
        stored in a MockSumModel instance.
        """
        sum_model = MockSumModel.get_by_id(SUM_MODEL_ID)
        return sum_model.total if sum_model else 0

    def _populate_data(self):
        """Populate the datastore with four MockNumbersModel instances."""
        MockNumbersModel(number=1).put()
        MockNumbersModel(number=2).put()
        MockNumbersModel(number=1).put()
        MockNumbersModel(number=2).put()


class SampleMapReduceJobManager(jobs.BaseMapReduceJobManager):
    """Test job that counts the total number of explorations."""

    @classmethod
    def entity_classes_to_map_over(cls):
        return [exp_models.ExplorationModel]

    @staticmethod
    def map(item):
        current_class = SampleMapReduceJobManager
        if current_class.entity_created_before_job_queued(item):
            yield ('sum', 1)

    @staticmethod
    def reduce(key, values):
        yield (key, sum([int(value) for value in values]))


class MapReduceJobForCheckingParamNames(jobs.BaseMapReduceOneOffJobManager):
    """Test job that checks correct param_name."""

    @classmethod
    def entity_classes_to_map_over(cls):
        return [exp_models.ExplorationModel]

    @staticmethod
    def map(item):
        jobs.BaseMapReduceOneOffJobManager.get_mapper_param('exp_id')


class ParamNameTests(test_utils.GenericTestBase):

    def test_job_raises_error_with_invalid_param_name(self):
        exploration = exp_domain.Exploration.create_default_exploration(
            'exp_id_1')
        exp_services.save_new_exploration('owner_id', exploration)

        job_id = MapReduceJobForCheckingParamNames.create_new()
        params = {
            'invalid_param_name': 'exp_id_1'
        }

        MapReduceJobForCheckingParamNames.enqueue(
            job_id, additional_job_params=params)

        self.assertEqual(
            self.count_jobs_in_mapreduce_taskqueue(
                taskqueue_services.QUEUE_NAME_ONE_OFF_JOBS), 1)

        assert_raises_regexp_context_manager = self.assertRaisesRegexp(
            Exception, 'MapReduce task failed: Task<.*>')

        with assert_raises_regexp_context_manager:
            self.process_and_flush_pending_mapreduce_tasks()

    def test_job_with_correct_param_name(self):
        exploration = exp_domain.Exploration.create_default_exploration(
            'exp_id_1')
        exp_services.save_new_exploration('owner_id', exploration)

        job_id = MapReduceJobForCheckingParamNames.create_new()
        params = {
            'exp_id': 'exp_id_1'
        }

        MapReduceJobForCheckingParamNames.enqueue(
            job_id, additional_job_params=params)

        self.assertEqual(
            self.count_jobs_in_mapreduce_taskqueue(
                taskqueue_services.QUEUE_NAME_ONE_OFF_JOBS), 1)

        self.process_and_flush_pending_mapreduce_tasks()

        self.assertEqual(
            self.count_jobs_in_mapreduce_taskqueue(
                taskqueue_services.QUEUE_NAME_ONE_OFF_JOBS), 0)


class MapReduceJobIntegrationTests(test_utils.GenericTestBase):
    """Tests MapReduce jobs end-to-end."""

    def setUp(self):
        """Create an exploration so that there is something to count."""
        super(MapReduceJobIntegrationTests, self).setUp()
        exploration = exp_domain.Exploration.create_default_exploration(
            'exp_id')
        exp_services.save_new_exploration('owner_id', exploration)
        self.process_and_flush_pending_mapreduce_tasks()

    def test_count_all_explorations(self):
        job_id = SampleMapReduceJobManager.create_new()
        SampleMapReduceJobManager.enqueue(
            job_id, taskqueue_services.QUEUE_NAME_DEFAULT)
        self.assertEqual(
            self.count_jobs_in_mapreduce_taskqueue(
                taskqueue_services.QUEUE_NAME_DEFAULT), 1)
        self.process_and_flush_pending_mapreduce_tasks()

        self.assertEqual(jobs.get_job_output(job_id), ['[u\'sum\', 1]'])
        self.assertEqual(
            SampleMapReduceJobManager.get_status_code(job_id),
            jobs.STATUS_CODE_COMPLETED)

    def test_base_map_reduce_job_manager_entity_classes_to_map_over_raise_error(
            self):
        with self.assertRaisesRegexp(
            NotImplementedError,
            'Classes derived from BaseMapReduceJobManager must implement '
            'entity_classes_to_map_over()'):
            jobs.BaseMapReduceJobManager.entity_classes_to_map_over()

    def test_base_map_reduce_job_manager_map_raise_error(self):
        with self.assertRaisesRegexp(
            NotImplementedError,
            'Classes derived from BaseMapReduceJobManager must implement '
            'map as a @staticmethod.'):
            jobs.BaseMapReduceJobManager.map('item')

    def test_base_map_reduce_job_manager_reduce_raise_error(self):
        with self.assertRaisesRegexp(
            NotImplementedError,
            'Classes derived from BaseMapReduceJobManager must implement '
            'reduce as a @staticmethod'):
            jobs.BaseMapReduceJobManager.reduce('key', [])

    def test_raises_error_with_existing_mapper_param(self):
        job_id = SampleMapReduceJobManager.create_new()
        with self.assertRaisesRegexp(
            Exception,
            'Additional job param entity_kinds shadows an existing mapper '
            'param'):
            SampleMapReduceJobManager.enqueue(
                job_id, taskqueue_services.QUEUE_NAME_DEFAULT,
                additional_job_params={'entity_kinds': ''})


class JobRegistryTests(test_utils.GenericTestBase):
    """Tests job registry."""

    def test_each_one_off_class_is_subclass_of_base_job_manager(self):
        for klass in jobs_registry.ONE_OFF_JOB_MANAGERS:
            self.assertTrue(issubclass(klass, jobs.BaseJobManager))

    def test_is_abstract_method_raises_exception_for_abstract_classes(self):
        class TestMockAbstractClass(jobs.BaseJobManager):
            """A sample Abstract Class."""

            pass

        mock_abstract_base_classes = [TestMockAbstractClass]
        with self.assertRaisesRegexp(
            Exception,
            'Tried to directly create a job using the abstract base '
            'manager class TestMockAbstractClass, which is not allowed.'):
            with self.swap(
                jobs, 'ABSTRACT_BASE_CLASSES', mock_abstract_base_classes):
                TestMockAbstractClass.create_new()

    def test_each_one_off_class_is_not_abstract(self):
        for klass in jobs_registry.ONE_OFF_JOB_MANAGERS:
            klass.create_new()


class BaseMapReduceJobManagerForContinuousComputationsTests(
        test_utils.GenericTestBase):

    def test_raise_error_with_get_continuous_computation_class(self):
        with self.assertRaisesRegexp(
            NotImplementedError,
            re.escape(
                'Subclasses of '
                'BaseMapReduceJobManagerForContinuousComputations must '
                'implement the _get_continuous_computation_class() method.')):
            (
                jobs.BaseMapReduceJobManagerForContinuousComputations.  # pylint: disable=protected-access
                _get_continuous_computation_class()
            )

    def test_raise_error_with_post_cancel_hook(self):
        with self.assertRaisesRegexp(
            NotImplementedError,
            re.escape(
                'Subclasses of '
                'BaseMapReduceJobManagerForContinuousComputations must '
                'implement the _get_continuous_computation_class() method.')):
            (
                jobs.BaseMapReduceJobManagerForContinuousComputations.  # pylint: disable=protected-access
                _post_cancel_hook('job_id', 'cancel message')
            )

    def test_raise_error_with_post_failure_hook(self):
        with self.assertRaisesRegexp(
            NotImplementedError,
            re.escape(
                'Subclasses of '
                'BaseMapReduceJobManagerForContinuousComputations must '
                'implement the _get_continuous_computation_class() method.')):
            (
                jobs.BaseMapReduceJobManagerForContinuousComputations.  # pylint: disable=protected-access
                _post_failure_hook('job_id')
            )


class BaseContinuousComputationManagerTests(test_utils.GenericTestBase):

    def test_raise_error_with_get_event_types_listened_to(self):
        with self.assertRaisesRegexp(
            NotImplementedError,
            re.escape(
                'Subclasses of BaseContinuousComputationManager must implement '
                'get_event_types_listened_to(). This method should return a '
                'list of strings, each representing an event type that this '
                'class subscribes to.')):
            jobs.BaseContinuousComputationManager.get_event_types_listened_to()

    def test_raise_error_with_get_realtime_datastore_class(self):
        with self.assertRaisesRegexp(
            NotImplementedError,
            re.escape(
                'Subclasses of BaseContinuousComputationManager must implement '
                '_get_realtime_datastore_class(). This method should return '
                'the datastore class to be used by the realtime layer.')):
            jobs.BaseContinuousComputationManager._get_realtime_datastore_class(  # pylint: disable=protected-access
                )

    def test_raise_error_with_get_batch_job_manager_class(self):
        with self.assertRaisesRegexp(
            NotImplementedError,
            re.escape(
                'Subclasses of BaseContinuousComputationManager must implement '
                '_get_batch_job_manager_class(). This method should return the'
                'manager class for the continuously-running batch job.')):
            jobs.BaseContinuousComputationManager._get_batch_job_manager_class()  # pylint: disable=protected-access

    def test_raise_error_with_handle_incoming_event(self):
        with self.assertRaisesRegexp(
            NotImplementedError,
            re.escape(
                'Subclasses of BaseContinuousComputationManager must implement '
                '_handle_incoming_event(...). Please check the docstring of '
                'this method in jobs.BaseContinuousComputationManager for '
                'important developer information.')):
            jobs.BaseContinuousComputationManager._handle_incoming_event(  # pylint: disable=protected-access
                1, 'event_type')


class TwoClassesMapReduceJobManager(jobs.BaseMapReduceJobManager):
    """A test job handler that counts entities in two datastore classes."""

    @classmethod
    def entity_classes_to_map_over(cls):
        return [exp_models.ExplorationModel, exp_models.ExplorationRightsModel]

    @staticmethod
    def map(item):
        yield ('sum', 1)

    @staticmethod
    def reduce(key, values):
        yield [key, sum([int(value) for value in values])]


class TwoClassesMapReduceJobIntegrationTests(test_utils.GenericTestBase):
    """Tests MapReduce jobs using two classes end-to-end."""

    def setUp(self):
        """Create an exploration so that there is something to count."""
        super(TwoClassesMapReduceJobIntegrationTests, self).setUp()
        exploration = exp_domain.Exploration.create_default_exploration(
            'exp_id')
        # Note that this ends up creating an entry in the
        # ExplorationRightsModel as well.
        exp_services.save_new_exploration('owner_id', exploration)
        self.process_and_flush_pending_mapreduce_tasks()

    def test_count_entities(self):
        self.assertEqual(exp_models.ExplorationModel.query().count(), 1)
        self.assertEqual(exp_models.ExplorationRightsModel.query().count(), 1)

        job_id = TwoClassesMapReduceJobManager.create_new()
        TwoClassesMapReduceJobManager.enqueue(
            job_id, taskqueue_services.QUEUE_NAME_DEFAULT)
        self.assertEqual(
            self.count_jobs_in_mapreduce_taskqueue(
                taskqueue_services.QUEUE_NAME_DEFAULT),
            1)
        self.process_and_flush_pending_mapreduce_tasks()

        self.assertEqual(
            TwoClassesMapReduceJobManager.get_output(job_id), ['[u\'sum\', 2]'])
        self.assertEqual(
            TwoClassesMapReduceJobManager.get_status_code(job_id),
            jobs.STATUS_CODE_COMPLETED)


class MockStartExplorationRealtimeModel(
        jobs.BaseRealtimeDatastoreClassForContinuousComputations):
    count = datastore_services.IntegerProperty(default=0)


class MockStartExplorationMRJobManager(
        jobs.BaseMapReduceJobManagerForContinuousComputations):

    @classmethod
    def _get_continuous_computation_class(cls):
        return StartExplorationEventCounter

    @classmethod
    def entity_classes_to_map_over(cls):
        return [stats_models.StartExplorationEventLogEntryModel]

    @staticmethod
    def map(item):
        current_class = MockStartExplorationMRJobManager
        if current_class.entity_created_before_job_queued(item):
            yield (
                item.exploration_id, {
                    'event_type': item.event_type,
                })

    @staticmethod
    def reduce(key, stringified_values):
        started_count = 0
        for value_str in stringified_values:
            value = ast.literal_eval(value_str)
            if value['event_type'] == feconf.EVENT_TYPE_START_EXPLORATION:
                started_count += 1
        stats_models.ExplorationAnnotationsModel(
            id=key, num_starts=started_count).put()


class StartExplorationEventCounter(jobs.BaseContinuousComputationManager):
    """A continuous-computation job that counts 'start exploration' events.

    This class should only be used in tests.
    """

    @classmethod
    def get_event_types_listened_to(cls):
        return [feconf.EVENT_TYPE_START_EXPLORATION]

    @classmethod
    def _get_realtime_datastore_class(cls):
        return MockStartExplorationRealtimeModel

    @classmethod
    def _get_batch_job_manager_class(cls):
        return MockStartExplorationMRJobManager

    @classmethod
    def _kickoff_batch_job_after_previous_one_ends(cls):
        """Override this method so that it does not immediately start a
        new MapReduce job. Non-test subclasses should not do this.
        """
        pass

    @classmethod
    def _handle_incoming_event(
            cls, active_realtime_layer, event_type, exp_id, unused_exp_version,
            unused_state_name, unused_session_id, unused_params,
            unused_play_type):

        @transaction_services.run_in_transaction_wrapper
        def _increment_counter():
            """Increments the count, if the realtime model corresponding to the
            active real-time model id exists.
            """
            realtime_class = cls._get_realtime_datastore_class()
            realtime_model_id = realtime_class.get_realtime_id(
                active_realtime_layer, exp_id)

            realtime_class(
                id=realtime_model_id, count=1,
                realtime_layer=active_realtime_layer).put()

        _increment_counter()

    # Public query method.
    @classmethod
    def get_count(cls, exploration_id):
        """Return the number of 'start exploration' events received.

        Answers the query by combining the existing MR job output and the
        active realtime_datastore_class.
        """
        mr_model = stats_models.ExplorationAnnotationsModel.get(
            exploration_id, strict=False)
        realtime_model = cls._get_realtime_datastore_class().get(
            cls.get_active_realtime_layer_id(exploration_id), strict=False)

        answer = 0
        if mr_model is not None:
            answer += mr_model.num_starts
        if realtime_model is not None:
            answer += realtime_model.count
        return answer


class MockMRJobManager(jobs.BaseMapReduceJobManagerForContinuousComputations):

    @classmethod
    def _get_continuous_computation_class(cls):
        return MockContinuousComputationManager

    @classmethod
    def entity_classes_to_map_over(cls):
        return []


class MockContinuousComputationManager(jobs.BaseContinuousComputationManager):
    TIMES_RUN = 0

    @classmethod
    def get_event_types_listened_to(cls):
        return []

    @classmethod
    def _get_realtime_datastore_class(cls):
        return MockStartExplorationRealtimeModel

    @classmethod
    def _get_batch_job_manager_class(cls):
        return MockMRJobManager

    @classmethod
    def _kickoff_batch_job_after_previous_one_ends(cls):
        if cls.TIMES_RUN < 2:
            (
                super(cls, MockContinuousComputationManager)
                ._kickoff_batch_job_after_previous_one_ends()
            )
            cls.TIMES_RUN = cls.TIMES_RUN + 1


class ContinuousComputationTests(test_utils.GenericTestBase):
    """Tests continuous computations for 'start exploration' events."""

    EXP_ID = 'exp_id'

    ALL_CC_MANAGERS_FOR_TESTS = [
        StartExplorationEventCounter, MockContinuousComputationManager]

    def setUp(self):
        """Create an exploration and register the event listener manually."""
        super(ContinuousComputationTests, self).setUp()

        exploration = exp_domain.Exploration.create_default_exploration(
            self.EXP_ID)
        exp_services.save_new_exploration('owner_id', exploration)
        self.process_and_flush_pending_mapreduce_tasks()

    def test_cannot_get_entity_with_invalid_id(self):
        with self.assertRaisesRegexp(
            ValueError, 'Invalid realtime id: invalid_entity_id'):
            MockStartExplorationRealtimeModel.get('invalid_entity_id')

    def test_cannot_put_realtime_class_with_invalid_id(self):
        realtime_class = MockStartExplorationRealtimeModel

        with self.assertRaisesRegexp(
            Exception,
            'Realtime layer 1 does not match realtime id '
            'invalid_realtime_model_id'):
            realtime_class(
                id='invalid_realtime_model_id', count=1, realtime_layer=1).put()

    def test_continuous_computation_workflow(self):
        """An integration test for continuous computations."""
        with self.swap(
            jobs_registry, 'ALL_CONTINUOUS_COMPUTATION_MANAGERS',
            self.ALL_CC_MANAGERS_FOR_TESTS
            ):
            self.assertEqual(
                StartExplorationEventCounter.get_count(self.EXP_ID), 0)

            # Record an event. This will put the event in the task queue.
            event_services.StartExplorationEventHandler.record(
                self.EXP_ID, 1, feconf.DEFAULT_INIT_STATE_NAME, 'session_id',
                {}, feconf.PLAY_TYPE_NORMAL)
            self.assertEqual(
                StartExplorationEventCounter.get_count(self.EXP_ID), 0)
            self.assertEqual(
                self.count_jobs_in_taskqueue(
                    taskqueue_services.QUEUE_NAME_EVENTS), 1)

            # When the task queue is flushed, the data is recorded in the two
            # realtime layers.
            self.process_and_flush_pending_tasks()
            self.process_and_flush_pending_mapreduce_tasks()
            self.assertEqual(
                self.count_jobs_in_taskqueue(
                    taskqueue_services.QUEUE_NAME_EVENTS), 0)
            self.assertEqual(
                StartExplorationEventCounter.get_count(self.EXP_ID), 1)
            self.assertEqual(MockStartExplorationRealtimeModel.get(
                '0:%s' % self.EXP_ID).count, 1)
            self.assertEqual(MockStartExplorationRealtimeModel.get(
                '1:%s' % self.EXP_ID).count, 1)

            # The batch job has not run yet, so no entity for self.EXP_ID will
            # have been created in the batch model yet.
            with self.assertRaisesRegexp(
                base_models.BaseModel.EntityNotFoundError,
                'Entity for class ExplorationAnnotationsModel with id exp_id '
                'not found'):
                stats_models.ExplorationAnnotationsModel.get(self.EXP_ID)

            # Launch the batch computation.
            batch_job_id = StartExplorationEventCounter.start_computation()
            # Data in realtime layer 0 is still there.
            self.assertEqual(MockStartExplorationRealtimeModel.get(
                '0:%s' % self.EXP_ID).count, 1)
            # Data in realtime layer 1 has been deleted.
            self.assertIsNone(MockStartExplorationRealtimeModel.get(
                '1:%s' % self.EXP_ID, strict=False))

            self.assertEqual(
                self.count_jobs_in_mapreduce_taskqueue(
                    taskqueue_services.QUEUE_NAME_CONTINUOUS_JOBS), 1)
            self.assertTrue(
                MockStartExplorationMRJobManager.is_active(batch_job_id))
            self.assertFalse(
                MockStartExplorationMRJobManager.has_finished(batch_job_id))
            self.process_and_flush_pending_mapreduce_tasks()
            self.assertFalse(
                MockStartExplorationMRJobManager.is_active(batch_job_id))
            self.assertTrue(
                MockStartExplorationMRJobManager.has_finished(batch_job_id))
            self.assertEqual(
                stats_models.ExplorationAnnotationsModel.get(
                    self.EXP_ID).num_starts, 1)

            # The overall count is still 1.
            self.assertEqual(
                StartExplorationEventCounter.get_count(self.EXP_ID), 1)
            # Data in realtime layer 0 has been deleted.
            self.assertIsNone(MockStartExplorationRealtimeModel.get(
                '0:%s' % self.EXP_ID, strict=False))
            # Data in realtime layer 1 has been deleted.
            self.assertIsNone(MockStartExplorationRealtimeModel.get(
                '1:%s' % self.EXP_ID, strict=False))

    def test_events_coming_in_while_batch_job_is_running(self):
        with self.swap(
            jobs_registry, 'ALL_CONTINUOUS_COMPUTATION_MANAGERS',
            self.ALL_CC_MANAGERS_FOR_TESTS
            ):
            # Currently no events have been recorded.
            self.assertEqual(
                StartExplorationEventCounter.get_count(self.EXP_ID), 0)

            # We will be handling the event for recording exploration start
            # events by calling StartExplorationEventLogEntryModel. This
            # will record an event while this job is in this queue.
            stats_models.StartExplorationEventLogEntryModel.create(
                self.EXP_ID, 1, feconf.DEFAULT_INIT_STATE_NAME, 'session_id',
                {}, feconf.PLAY_TYPE_NORMAL)
            StartExplorationEventCounter.on_incoming_event(
                event_services.StartExplorationEventHandler.EVENT_TYPE,
                self.EXP_ID, 1, feconf.DEFAULT_INIT_STATE_NAME, 'session_id',
                {}, feconf.PLAY_TYPE_NORMAL)
            # The overall count is now 1.
            self.assertEqual(
                StartExplorationEventCounter.get_count(self.EXP_ID), 1)

            # Finish the job.
            self.process_and_flush_pending_mapreduce_tasks()
            # When the batch job completes, the overall count is still 1.
            self.assertEqual(
                StartExplorationEventCounter.get_count(self.EXP_ID), 1)
            # The batch job result should still be 0, since the event arrived
            # after the batch job started.
            with self.assertRaisesRegexp(
                base_models.BaseModel.EntityNotFoundError,
                'Entity for class ExplorationAnnotationsModel with id exp_id '
                'not found'):
                stats_models.ExplorationAnnotationsModel.get(self.EXP_ID)

    def test_cannot_start_new_job_while_existing_job_still_running(self):
        with self.swap(
            jobs_registry, 'ALL_CONTINUOUS_COMPUTATION_MANAGERS',
            self.ALL_CC_MANAGERS_FOR_TESTS
            ):
            StartExplorationEventCounter.start_computation()
            with self.assertRaisesRegexp(
                Exception,
                'Attempted to start computation StartExplorationEventCounter, '
                'which is already running'):
                StartExplorationEventCounter.start_computation()

            self.process_and_flush_pending_mapreduce_tasks()
            StartExplorationEventCounter.stop_computation('admin_user_id')

    def test_get_continuous_computations_info_with_existing_model(self):
        job_models.ContinuousComputationModel(
            id='StartExplorationEventCounter').put()
        continuous_computations_data = jobs.get_continuous_computations_info(
            [StartExplorationEventCounter])

        expected_continuous_computations_data = [{
            'active_realtime_layer_index': 0,
            'computation_type': 'StartExplorationEventCounter',
            'status_code': 'idle',
            'is_startable': True,
            'is_stoppable': False,
            'last_finished_msec': None,
            'last_started_msec': None,
            'last_stopped_msec': None
        }]

        self.assertEqual(
            expected_continuous_computations_data, continuous_computations_data)

    def test_failing_continuous_job(self):
        observed_log_messages = []

        def _mock_logging_function(msg, *args):
            """Mocks logging.error()."""
            observed_log_messages.append(msg % args)

        StartExplorationEventCounter.start_computation()

        status = StartExplorationEventCounter.get_status_code()
        self.assertEqual(
            status, job_models.CONTINUOUS_COMPUTATION_STATUS_CODE_RUNNING)

        with self.swap(logging, 'error', _mock_logging_function):
            StartExplorationEventCounter.on_batch_job_failure()

        self.run_but_do_not_flush_pending_mapreduce_tasks()
        StartExplorationEventCounter.stop_computation('admin_user_id')

        self.assertEqual(
            observed_log_messages, ['Job StartExplorationEventCounter failed.'])

    def test_cancelling_continuous_job(self):
        observed_log_messages = []

        def _mock_logging_function(msg, *args):
            """Mocks logging.error()."""
            observed_log_messages.append(msg % args)

        StartExplorationEventCounter.start_computation()

        status = StartExplorationEventCounter.get_status_code()
        self.assertEqual(
            status, job_models.CONTINUOUS_COMPUTATION_STATUS_CODE_RUNNING)

        with self.swap(logging, 'info', _mock_logging_function):
            StartExplorationEventCounter.on_batch_job_canceled()

        self.run_but_do_not_flush_pending_mapreduce_tasks()
        StartExplorationEventCounter.stop_computation('admin_user_id')

        self.assertEqual(
            observed_log_messages,
            ['Job StartExplorationEventCounter canceled.'])

    def test_kickoff_batch_job_after_previous_one_ends(self):
        with self.swap(
            jobs_registry, 'ALL_CONTINUOUS_COMPUTATION_MANAGERS',
            self.ALL_CC_MANAGERS_FOR_TESTS
        ):
            self.assertEqual(MockContinuousComputationManager.TIMES_RUN, 0)
            MockContinuousComputationManager.start_computation()
            MockContinuousComputationManager.on_batch_job_completion()
            status = MockContinuousComputationManager.get_status_code()

            self.assertEqual(
                status, job_models.CONTINUOUS_COMPUTATION_STATUS_CODE_RUNNING)

            self.run_but_do_not_flush_pending_mapreduce_tasks()
            MockContinuousComputationManager.stop_computation('admin_user_id')
            status = MockContinuousComputationManager.get_status_code()

            self.assertEqual(
                status, job_models.CONTINUOUS_COMPUTATION_STATUS_CODE_IDLE)
            self.assertEqual(MockContinuousComputationManager.TIMES_RUN, 1)


# TODO(sll): When we have some concrete ContinuousComputations running in
# production, add an integration test to ensure that the registration of event
# handlers in the main codebase is happening correctly.
