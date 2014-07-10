# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.
from nap.url import Url
import pprint

pp = pprint.PrettyPrinter(indent=4)


class JsonApi(Url):
    def before_request(self, method, request_kwargs):
        return {'params': {'format': 'json'}}

    def after_request(self, response):
        if response.status_code != 200:
            response.raise_for_status()

        return response.json()


class Fabrik(object):
    def __init__(self):
        self.types = {}
        """
        All types stored as a dictonary with id as key.
        Possible keys: id, name, prefix, submission_date, class, slide
        """

        self.types_prefix = {}
        """Mapping from prefix to id of type."""

        self.applications = []
        """
        List of applications (as a dictionary).
        Possible keys: appl_url, author, author_name, created, discussion, id, lqfbinitiative_set, number, reasons,
        status, status_name, submitted, text, text_html, title, typ (id, name, prefix, submission_date), typ_name,
        updated, url
        """

        self.api = JsonApi('http://lptfabrik.piraten-lsa.de/')

    def fetch_types(self):

        res_types = self.api.get('api/typ/')

        for typ in res_types:
            typ_id = typ['id']
            self.types[typ_id] = typ
            self.types[typ_id]['class'] = ''
            self.types[typ_id]['slide'] = ''
            self.types_prefix[typ['prefix']] = typ_id

    def fetch_applications(self):
        self.applications = self.api.get('api/appl/?status=S')


class VL(object):
    # slides (list) - done
    # slides:MOTIONCLASSSLIDEID (hash) - done
    # slides:MOTIONCLASSSLIDEID:children (list) - done
    # slides:motion-MOTIONSLIDEID (hash) - done
    # motionclasses (list) - done
    # motionclasses:MOTIONCLASSID (hash) - done
    # motionclasses:MOTIONCLASSID:motions (list) - done
    # motions:MOTIONID (hash) - done

    def __init__(self, fabrik):
        assert isinstance(fabrik, Fabrik)
        self.fabrik = fabrik

        import redis
        self.r = redis.StrictRedis(host='localhost', port=6379, db=0, decode_responses=True)

        self.debug = False

    def __printdebug__(self, line):
        if self.debug:
            print(line)

    @staticmethod
    def get_random_id(count=11):
        from random import choice
        return ''.join([choice('abcdefghijklmnopqrstuvwxyz0123456789') for _ in range(count)])

    def prepare_motion_classes(self):
        # get count of existing motion classes
        motionclasses_count = self.r.llen('motionclasses')
        # get all ids of existing motion classes
        motionclasses_ids = self.r.lrange('motionclasses', 0, motionclasses_count - 1)

        assert isinstance(motionclasses_ids, list)

        for motionclass_id in motionclasses_ids:
            assert isinstance(motionclass_id, str)
            # get all key-values of motion class
            motionclass = self.r.hgetall('motionclasses:' + motionclass_id)

            if 'idPrefix' not in motionclass or 'slideid' not in motionclass:
                continue

            # create mapping of all existing motion classes
            try:
                typ_id = self.fabrik.types_prefix[motionclass['idPrefix']]
                typ = self.fabrik.types[typ_id]
                typ['class'] = motionclass_id
                typ['slide'] = motionclass['slideid']
                self.__printdebug__('typ in both found: {}'.format(motionclass['idPrefix']))
            except KeyError:
                self.__printdebug__('typ in redis but not in api: {}'.format(motionclass['idPrefix']))

        # create motion classes for all not existing types
        for key, typ in self.fabrik.types.items():
            assert isinstance(typ, dict)
            assert 'prefix' in typ

            if 'class' not in typ or typ['class'] == '':
                self.__printdebug__('creating typ in redis: {}'.format(typ['prefix']))
                self.create_motion_class(typ)

    def create_not_existing_motions(self):
        for appl in self.fabrik.applications:
            assert isinstance(appl, dict)
            assert 'number' in appl

            if not self.r.exists('motions:' + appl['number']):
                self.__printdebug__('creating application in redis: {}'.format(appl['number']))
                self.create_motion(appl)
            else:
                self.__printdebug__('updating application in redis: {}'.format(appl['number']))
                self.update_motion(appl)

    def create_motion_class(self, typ):
        assert isinstance(typ, dict)
        assert 'name' in typ
        assert 'prefix' in typ

        # random string -> test if already exists
        motionclass_slide_id = VL.get_random_id()
        while self.r.exists('slides:' + motionclass_slide_id):
            motionclass_slide_id = VL.get_random_id()

        self.r.hmset('slides:' + motionclass_slide_id, {
            'hidden': 'false',
            'isdone': 'false',
            'type': 'agenda',
            'hide': 'false',
            'title': typ['name']
        })

        self.r.rpush('slides', motionclass_slide_id)

        # random string -> test if already exists
        motionclass_id = VL.get_random_id()
        while self.r.exists('motionclasses:' + motionclass_id):
            motionclass_id = VL.get_random_id()

        self.r.hmset('motionclasses:' + motionclass_id, {
            'title': typ['name'],
            'idPrefix': typ['prefix'],
            'slideid': motionclass_slide_id
        })

        self.r.rpush('motionclasses', motionclass_id)

        typ['class'] = motionclass_id
        typ['slide'] = motionclass_slide_id

    def create_motion(self, appl):
        assert isinstance(appl, dict)
        assert 'number' in appl
        assert 'typ' in appl
        assert 'id' in appl['typ']
        assert appl['typ']['id'] in self.fabrik.types
        assert 'title' in appl
        assert 'text_html' in appl
        assert 'author_name' in appl
        assert 'reasons' in appl

        typ = self.fabrik.types[appl['typ']['id']]

        assert 'class' in typ
        assert 'slide' in typ

        motionclass = typ['class']

        self.r.hmset('motions:' + appl['number'], {
            'classid': motionclass,
            'title': appl['title'],
            'status': 'open',
            'text': appl['text_html'],
            'submitter': appl['author_name'],
            'hide': 'false',
            'argumentation': appl['reasons']
        })

        self.r.rpush('motionclasses:' + motionclass + ':motions', appl['number'])

        self.r.hmset('slides:motion-' + appl['number'], {
            'isdone': 'false',
            'hidden': 'false',
            'parentid': typ['slide'],
            'type': 'motion',
            'title': appl['number'] + ': ' + appl['title'],
            'motionid': appl['number']
        })

        self.r.rpush('slides:' + typ['slide'] + ':children', 'motion-' + appl['number'])

    def update_motion(self, appl):
        assert isinstance(appl, dict)
        assert 'number' in appl
        assert 'title' in appl
        assert 'text_html' in appl
        assert 'author_name' in appl
        assert 'reasons' in appl

        self.r.hmset('motions:' + appl['number'], {
            'title': appl['title'],
            'text': appl['text_html'],
            'submitter': appl['author_name'],
            'argumentation': appl['reasons']
        })

        self.r.hmset('slides:motion-' + appl['number'], {
            'title': appl['number'] + ': ' + appl['title'],
        })

if __name__ == "__main__":
    f = Fabrik()
    f.fetch_types()
    f.fetch_applications()

    vl = VL(f)
    vl.debug = True
    vl.prepare_motion_classes()
    vl.create_not_existing_motions()