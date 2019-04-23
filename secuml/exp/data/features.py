# SecuML
# Copyright (C) 2016-2019  ANSSI
#
# SecuML is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.
#
# SecuML is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License along
# with SecuML. If not, see <http://www.gnu.org/licenses/>.

import csv
import numpy as np

from secuml.core.data.features import Features
from secuml.core.data.ids import Ids
from secuml.exp.data import get_dataset_ids
from secuml.exp.tools.db_tables import InstancesAlchemy


class FeaturesFromExp(Features):

    def __init__(self, exp, instance_ids=None, streaming=False,
                 stream_batch=None):
        if instance_ids is None:
            dataset_id = exp.exp_conf.dataset_conf.dataset_id
            instance_ids = Ids(get_dataset_ids(exp.session, dataset_id))
        num_instances = instance_ids.num_instances()
        features_files = exp.exp_conf.features_conf.files
        if streaming:
            values = FeaturesFromExp.get_matrix_iterator(features_files,
                                                         num_instances)
        else:
            values = FeaturesFromExp.get_matrix(features_files, num_instances)
        Features.__init__(self, values, exp.exp_conf.features_conf.info,
                          instance_ids, streaming=streaming,
                          stream_batch=stream_batch)

    @staticmethod
    def get_matrix(features_files, num_instances):
        features = None
        iterator = FeaturesFromExp.get_matrix_iterator(features_files,
                                                       num_instances)
        for row in iterator:
            if features is None:
                features = row
            else:
                features = np.vstack((features, row))
        return features

    @staticmethod
    def get_matrix_iterator(features_files, num_instances):
        # Init csv readers.
        readers_masks = []
        for _, f_path, f_mask in features_files:
            f = open(f_path, 'r')
            f.readline()  # skip header
            f_reader = csv.reader(f, quoting=csv.QUOTE_NONNUMERIC)
            readers_masks.append((f, f_reader, f_mask))
        # Read csv files.
        for _ in range(num_instances):
            row = None
            for _, f_reader, f_mask in readers_masks:
                f_row = np.array(next(f_reader)[1:])[f_mask]
                if row is None:
                    row = f_row
                else:
                    row = np.hstack((row, f_row))
            yield row
        # Close the csv files.
        for f, _, _ in readers_masks:
            f.close()

    @staticmethod
    def get_instance(exp, instance_id):
        dataset_id = exp.exp_conf.dataset_conf.dataset_id
        query = exp.session.query(InstancesAlchemy)
        query = query.filter(InstancesAlchemy.dataset_id == dataset_id)
        query = query.filter(InstancesAlchemy.id == instance_id)
        row_number = query.one().row_number
        values = []
        features_conf = exp.exp_conf.features_conf
        for _, f_path, f_mask in features_conf.files:
            line = 1
            with open(f_path, 'r') as f_file:
                next(f_file)  # skip header
                while line < row_number:
                    next(f_file)
                    line = line + 1
                row = next(f_file).rstrip(),
                features_reader = csv.reader(row)
                v = np.array(next(features_reader)[1:])
                if f_mask is not None:
                    v = v[f_mask]
                values = np.hstack((values, v))
        values = [float(x) for x in values]
        return features_conf.info.names, values
