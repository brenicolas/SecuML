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
import os
import numpy as np
import pandas as pd
from sqlalchemy.orm.exc import NoResultFound

from secuml.core.data.features import FeaturesInfo
from secuml.core.data.features import FeatureType
from secuml.exp.conf.features import InputFeaturesTypes
from secuml.exp.tools.db_tables import FeaturesSetsAlchemy
from secuml.exp.tools.db_tables import FeaturesFilesAlchemy
from secuml.exp.tools.db_tables import FeaturesAlchemy
from secuml.exp.tools.exp_exceptions import SecuMLexpException
from secuml.exp.tools.exp_exceptions import UpdatedDirectory
from secuml.exp.tools.exp_exceptions import UpdatedFile

from . import compute_hash
from .features import FeaturesFromExp


class FeaturesNotFound(SecuMLexpException):

    def __init__(self, input_path):
        self.input_path = input_path

    def __str__(self):
        return 'Invalid features path: %s does not exist.' % self.input_path


class InvalidDescription(SecuMLexpException):

    def __init__(self, input_path, message):
        self.input_path = input_path
        self.message = message

    def __str__(self):
        return 'Invalid description for %s. %s' % (self.input_path,
                                                   self.message)


class LoadFeatures(object):

    def __init__(self, exp_conf, secuml_conf, session):
        self.secuml_conf = secuml_conf
        self.dataset_conf = exp_conf.dataset_conf
        self.features_conf = exp_conf.features_conf
        self.session = session

    def load(self, num_instances):
        dataset_dir = self.dataset_conf.input_dir(self.secuml_conf)
        self.input_path = os.path.join(dataset_dir, 'features',
                                       self.features_conf.input_features)
        set_id, input_type = self._check()
        if set_id is None:
            set_id = self._load_features_set(input_type)
            self._load_features_files(set_id, input_type, num_instances)
        self.session.flush()
        self._set_features_conf(set_id, input_type)

    def _set_features_conf(self, set_id, input_type):
        self.features_conf.set_set_id(set_id)
        self.features_conf.set_input_type(input_type)
        # Set features_files_ids
        query = self.session.query(FeaturesFilesAlchemy)
        query = query.filter(FeaturesFilesAlchemy.set_id == set_id)
        query = query.order_by(FeaturesFilesAlchemy.id)
        files = [(r.id, r.path) for r in query.all()]
        # Set filters in / out
        filter_in = self._get_filter(self.features_conf.filter_in_f)
        filter_out = self._get_filter(self.features_conf.filter_out_f)
        # Set masks / features info
        masks = [None for _ in range(len(files))]
        all_info = FeaturesInfo([], [], [], [])
        for i, (f_id, f_path) in enumerate(files):
            mask, info = self._get_mask_info(f_id, f_path, filter_in,
                                             filter_out)
            masks[i] = mask
            all_info.union(info)
        self.features_conf.set_info(all_info)
        self.features_conf.set_files([(id_, path, masks[i])
                                     for i, (id_, path) in enumerate(files)])

    def _get_mask_info(self, f_id, f_path, filter_in, filter_out):
        query = self.session.query(FeaturesAlchemy)
        query = query.filter(FeaturesAlchemy.file_id == f_id)
        query = query.order_by(FeaturesAlchemy.id)
        features = [(r.id, r.user_id, r.name, r.description, r.type)
                    for r in query.all()]
        user_ids = [r[1] for r in features]
        if filter_in is not None:
            mask = [user_id in filter_in for user_id in user_ids]
        elif filter_out is not None:
            mask = [user_id not in filter_out for user_id in user_ids]
        else:
            mask = [True for _ in user_ids]
        selection = [(id_, name, desc, FeatureType[type_])
                     for i, (id_, _, name, desc, type_) in enumerate(features)
                     if mask[i]]
        if selection:
            info = FeaturesInfo(*zip(*selection))
        else:
            info = FeaturesInfo([], [], [], [])
        return mask, info

    def _get_filter(self, filter_file):
        if filter_file is None:
            return None
        dataset_dir = self.dataset_conf.input_dir(self.secuml_conf)
        features_dir = os.path.join(dataset_dir, 'features')
        with open(os.path.join(features_dir, filter_file)) as f:
            return [r.rstrip() for r in f.readlines()]

    def _load_features_set(self, input_type):
        features_set = FeaturesSetsAlchemy(
                                    dataset_id=self.dataset_conf.dataset_id,
                                    name=self.features_conf.input_features,
                                    type=input_type.name)
        self.session.add(features_set)
        self.session.flush()
        return features_set.id

    def _load_features_files(self, set_id, input_type, num_instances):
        if input_type == InputFeaturesTypes.file:
            files = [(self.input_path, self.features_conf.input_features)]
        elif input_type == InputFeaturesTypes.dir:
            files = [(os.path.join(self.input_path, f), f)
                     for f in os.listdir(self.input_path)
                     if '_description' not in f]
        for file_path, filename in files:
            self._load_features_file(set_id, file_path, filename,
                                     num_instances)

    def _load_features_file(self, set_id, file_path, filename, num_instances):
        file_hash = compute_hash(file_path)
        features_file = FeaturesFilesAlchemy(set_id=set_id, filename=filename,
                                             path=file_path, hash=file_hash)
        self.session.add(features_file)
        self.session.flush()
        self._load_features(set_id, features_file.id, file_path, num_instances)

    def _load_features(self, set_id, file_id, file_path, num_instances):
        user_ids, names, descrips, types = self._get_ids_types(file_path,
                                                               num_instances)
        features = [FeaturesAlchemy(user_id=u_id, file_id=file_id,
                                    set_id=set_id, name=name, description=desc,
                                    type=type_.name)
                    for u_id, name, desc, type_ in zip(user_ids, names,
                                                       descrips, types)]
        self.session.bulk_save_objects(features)

    def _get_ids_types(self, file_path, num_instances):
        user_ids = None
        names = None
        descriptions = None
        types = None
        basename, _ = os.path.splitext(file_path)
        description_file = '%s_description.csv' % basename
        if os.path.isfile(description_file):
            with open(description_file, 'r') as f:
                df = pd.read_csv(f, header=0, index_col=0)
                user_ids = [str(i) for i in df.index.values]
                try:
                    names = df['name'].values
                except KeyError:
                    raise InvalidDescription(file_path,
                                             'The description file must '
                                             'contain a name column. ')
                try:
                    # The description column is not mandatory.
                    descriptions = df['description'].values
                except KeyError:
                    pass
                # The type column is not mandatory.
                try:
                    types = df['type'].values
                    try:
                        types = [FeatureType[t] for t in types]
                    except KeyError:
                        raise InvalidDescription(
                               file_path,
                               'Features types must be "binary" or "numeric".')
                except KeyError:
                    pass
        else:
            if self.features_conf.sparse:
                raise InvalidDescription(file_path,
                                         'A description file is required for '
                                         'sparse features. ')
        if not self.features_conf.sparse:
            with open(file_path, 'r') as f_file:
                features_reader = csv.reader(f_file)
                f_user_ids = next(features_reader)[1:]
                if user_ids is None:
                    user_ids = f_user_ids
                else:
                    if len(names) != len(f_user_ids):
                        raise InvalidDescription(file_path,
                                                 'There are %i features, '
                                                 'but %i descriptions. '
                                                 % (len(user_ids), len(names)))
                    if f_user_ids != user_ids:
                        raise InvalidDescription(file_path,
                                                 'The ids do not correspond, '
                                                 'or are not stored in the '
                                                 'same order. ')
        if names is None:
            names = user_ids
        if descriptions is None:
            descriptions = user_ids
        if types is None:
            types = self._get_types(file_path, num_instances)
        return user_ids, names, descriptions, types

    def _get_types(self, file_path, num_instances):
        features = FeaturesFromExp.get_matrix([(None, file_path, None)],
                                              num_instances,
                                              sparse=self.features_conf.sparse)
        num_features = features.shape[1]
        types = np.empty((num_features,), dtype=object)
        for i in range(num_features):
            values = features[:, i]
            if all(v in [0, 1] for v in values):
                types[i] = FeatureType.binary
            else:
                types[i] = FeatureType.numeric
        return types

    def _check(self):
        input_type = self._check_path_exists()
        set_id = self._check_already_loaded(input_type)
        if set_id is not None:
            self._check_hashes(set_id, input_type)
        return set_id, input_type

    def _check_path_exists(self):
        if os.path.isfile(self.input_path):
            return InputFeaturesTypes.file
        elif os.path.isdir(self.input_path):
            return InputFeaturesTypes.dir
        else:
            raise FeaturesNotFound(self.input_path)

    def _check_already_loaded(self, input_type):
        query = self.session.query(FeaturesSetsAlchemy)
        query = query.filter(FeaturesSetsAlchemy.dataset_id ==
                             self.dataset_conf.dataset_id)
        query = query.filter(FeaturesSetsAlchemy.name ==
                             self.features_conf.input_features)
        query = query.filter(FeaturesSetsAlchemy.type == input_type.name)
        try:
            set_id = query.one().id
        except NoResultFound:
            set_id = None
        return set_id

    def _check_hashes(self, set_id, input_type):
        query = self.session.query(FeaturesFilesAlchemy)
        query = query.filter(FeaturesFilesAlchemy.set_id == set_id)
        db_files = {r.filename: r.hash for r in query.all()}
        if input_type == InputFeaturesTypes.file:
            files = [self.features_conf.input_features]
            dataset_dir = self.dataset_conf.input_dir(self.secuml_conf)
            features_path = os.path.join(dataset_dir, 'features')
        elif input_type == InputFeaturesTypes.dir:
            files = [f for f in os.listdir(self.input_path)
                     if '_description' not in f]
            if len(files) != len(db_files):
                raise UpdatedDirectory(self.input_path, db_files.keys(), files)
            features_path = self.input_path
        for filename in files:
            file_path = os.path.join(features_path, filename)
            file_hash = compute_hash(file_path)
            if file_hash != db_files[filename]:
                raise UpdatedFile(file_path, self.dataset_conf.dataset)
