#!/usr/bin/env python3
"""Trusted BIND provenance at the signing boundary; also runs without pytest."""
from module_fixtures import *

from package_fixtures import (
    BIND_PROFILE as PROFILE, bind_records, creator_identity, write_resolved_target, write_build_metadata,
    write_json,
)

release_channel = load_module('release_channel', 'shared/release_channel.py')


class StageProvenanceTest(unittest.TestCase):
    def test_channel_rejects_untrusted_bind_provenance(self):
        creator = creator_identity('FreeBSD:14:amd64')
        for fault, error in [('fingerprint', 'fingerprint'), ('freebsd', 'trusted profile')]:
            with self.subTest(fault=fault):
                provenance = release_channel.bind920_profile.build_provenance(
                    PROFILE, '26.1', '14.4' if fault == 'freebsd' else '14.3',
                    'x86_64', creator, bind_records())
                if fault == 'fingerprint':
                    provenance['fingerprint'] = '0' * 64
                with self.assertRaisesRegex(ValueError, error):
                    release_channel.validate_bind_provenance(
                        provenance, PROFILE, '26.1', '14.3', creator)

    def test_channel_selection_uses_the_bind_pair_named_by_provenance(self):
        with tempfile.TemporaryDirectory() as temporary:
            packages = Path(temporary)
            records = bind_records('9.20.27')
            write_json(packages / 'bind920-provenance.json', {'packages': records})
            expected = ['bind-tools-9.20.27.pkg', 'bind920-9.20.27.pkg', 'os-bind-rp-26.7_1.pkg']
            for name in expected:
                (packages / name).touch()
            self.assertEqual(expected, [p.name for p in release_channel.select_channel_packages(packages)])

    def test_build_metadata_must_match_trusted_release_and_bind_provenance(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            metadata = root / 'build-metadata.txt'
            original = write_build_metadata(metadata)
            from git_fixtures import upstream_profile
            profile = upstream_profile('26.7')
            upstream = write_json(root / 'upstream.json', profile)
            provenance_record = dict(
                series='26.7', freebsd_release='15.1', package_creator=creator_identity(),
                packages={'bind920': {'version': '9.20.26_1'}})
            provenance = write_json(root / 'bind920-provenance.json', provenance_record)
            target = write_resolved_target(root / 'creator-target.json', profile)
            args = metadata, upstream, provenance, target, '26.7', 'a' * 40
            self.assertEqual(original, release_channel.validate_build_metadata(*args))
            for changes, record in [
                (dict(source_commit='b' * 40, upstream_commit='9' * 40), provenance_record),
                (dict(freebsd_release='15.2', tools_tag='26.7.2', creator_release='26.7.2'), dict(provenance_record, freebsd_release='15.2')),
                (dict(creator_ports_commit='5' * 40, creator_recipe_path='opnsense/pkg'),
                 dict(provenance_record, package_creator=creator_identity(origin='opnsense/pkg'))),
            ]:
                with self.subTest(changes=changes):
                    write_json(provenance, record)
                    write_build_metadata(metadata, **changes)
                    with self.assertRaisesRegex(ValueError, 'trusted release metadata'):
                        release_channel.validate_build_metadata(*args)



if __name__ == '__main__':
    unittest.main()
