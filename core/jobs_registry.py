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

"""Job registries."""

from __future__ import absolute_import  # pylint: disable=import-only-modules
from __future__ import unicode_literals  # pylint: disable=import-only-modules

from core.domain import activity_jobs_one_off
from core.domain import collection_jobs_one_off
from core.domain import exp_jobs_one_off
from core.domain import question_jobs_one_off
from core.domain import recommendations_jobs_one_off
from core.domain import skill_jobs_one_off
from core.domain import story_jobs_one_off
from core.domain import suggestion_jobs_one_off
from core.domain import topic_jobs_one_off
import python_utils

# List of all manager classes for one-off batch jobs for which to show controls
# on the admin dashboard.
ONE_OFF_JOB_MANAGERS = [
    activity_jobs_one_off.IndexAllActivitiesJobManager,
    collection_jobs_one_off.CollectionMigrationOneOffJob,
    exp_jobs_one_off.ExplorationMigrationJobManager,
    exp_jobs_one_off.ExpSnapshotsMigrationJob,
    question_jobs_one_off.FixQuestionImagesStorageOneOffJob,
    question_jobs_one_off.QuestionMigrationOneOffJob,
    question_jobs_one_off.QuestionSnapshotsMigrationJob,
    recommendations_jobs_one_off.ExplorationRecommendationsOneOffJob,
    skill_jobs_one_off.SkillMigrationOneOffJob,
    skill_jobs_one_off.SkillCommitCmdMigrationOneOffJob,
    story_jobs_one_off.StoryMigrationOneOffJob,
    suggestion_jobs_one_off.PopulateTranslationContributionStatsOneOffJob,
    suggestion_jobs_one_off.QuestionSuggestionMigrationJobManager,
    topic_jobs_one_off.TopicMigrationOneOffJob,
]

# List of all manager classes for prod validation one-off batch jobs for which
# to show controls on the admin dashboard.
AUDIT_JOB_MANAGERS = [
]

# List of all ContinuousComputation managers to show controls for on the
# admin dashboard.
# NOTE TO DEVELOPERS: When a new ContinuousComputation manager is defined,
# it should be registered here.
ALL_CONTINUOUS_COMPUTATION_MANAGERS = [
]


class ContinuousComputationEventDispatcher(python_utils.OBJECT):
    """Dispatches events to the relevant ContinuousComputation classes."""

    @classmethod
    def dispatch_event(cls, event_type, *args, **kwargs):
        """Dispatches an incoming event to the ContinuousComputation
        classes which listen to events of that type.

        Args:
            event_type: str. The type of the event.
            *args: list(*). Positional arguments to pass to on_incoming_event().
            **kwargs: *. Keyword arguments to pass to on_incoming_event().
        """
        for klass in ALL_CONTINUOUS_COMPUTATION_MANAGERS:
            if event_type in klass.get_event_types_listened_to():
                klass.on_incoming_event(event_type, *args, **kwargs)
