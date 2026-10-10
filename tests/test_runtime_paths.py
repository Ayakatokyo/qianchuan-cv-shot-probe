"""Runtime destinations stay under the caller's workspace and preserve old runs."""
from contextlib import redirect_stdout, redirect_stderr
from io import StringIO
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'scripts'))
import probe_core as core

class RuntimePathTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.workspace = Path(self.temp.name).resolve()

    def cli(self, args):
        output = StringIO()
        with redirect_stdout(output):
            code = core.main(args)
        return code, json.loads(output.getvalue())

    def test_default_is_absolute_unique_and_does_not_create_files(self):
        with patch.object(Path, 'cwd', return_value=self.workspace):
            first = core.new_run_root()
            second = core.new_run_root()
        platform = core.read(core.ROOT/'config/platform.json')['platform']
        expected = '千川素材分镜数据' if platform=='qianchuan' else '云图素材分镜数据'
        self.assertEqual(first.parent, self.workspace/expected)
        self.assertTrue(first.is_absolute())
        self.assertRegex(first.name, r'^run-\d{8}-\d{6}-[0-9a-f]{8}$')
        self.assertNotEqual(first, second)
        self.assertFalse(first.parent.exists())

    def test_explicit_root_and_exact_directory_have_distinct_meanings(self):
        root = self.workspace/'自定义根'
        run = core.new_run_root(output_root=root)
        self.assertEqual(run.parent, root)
        exact = self.workspace/'精确目录'
        self.assertEqual(core.new_run_root(output_dir=exact), exact)
        with patch.dict('os.environ', {'HOME': str(self.workspace)}):
            self.assertEqual(core.new_run_root(output_dir='~/精确目录'), exact)

    def test_relative_destination_resolves_against_real_cwd(self):
        previous = Path.cwd()
        try:
            os.chdir(self.workspace)
            self.assertEqual(core.new_run_root(output_dir='相对目录'), self.workspace/'相对目录')
            self.assertEqual(core.new_run_root(output_root='相对根').parent, self.workspace/'相对根')
        finally:
            os.chdir(previous)

    def test_existing_directory_is_preserved_without_creating_a_new_one(self):
        existing = self.workspace/'旧运行'
        existing.mkdir()
        evidence = existing/'evidence.json'
        evidence.write_text('original', encoding='utf-8')
        with self.assertRaises(core.ProbeError) as caught:
            core.new_run_root(output_dir=existing)
        self.assertEqual(caught.exception.code, 'output_dir_exists')
        self.assertEqual(evidence.read_text(), 'original')
        with patch.object(core, 'acquire') as acquire:
            code, result = self.cli(['acquire', '--request-file', 'unused.json', '--output-dir', str(existing)])
        self.assertEqual(code, 1)
        self.assertEqual(result['errorCode'], 'output_dir_exists')
        acquire.assert_not_called()

    def test_skill_and_symlink_targets_are_rejected_before_work(self):
        link = self.workspace/'skill-link'
        link.symlink_to(core.ROOT, target_is_directory=True)
        for value in (core.ROOT, core.ROOT/'runs/new', link/'new'):
            for keyword in ('output_root', 'output_dir'):
                with self.subTest(value=value, keyword=keyword):
                    with self.assertRaises(core.ProbeError) as caught:
                        core.new_run_root(**{keyword: value})
                    self.assertEqual(caught.exception.code, 'output_inside_skill')
        with patch.object(Path, 'cwd', return_value=core.ROOT):
            with self.assertRaises(core.ProbeError):
                core.new_run_root()
            # An explicit outside target remains valid even from the installation.
            self.assertEqual(core.new_run_root(output_dir=self.workspace/'new'), self.workspace/'new')

    def test_cli_output_flags_are_mutually_exclusive(self):
        for command, input_flag in (('acquire','--request-file'), ('run-batch','--request-file'), ('probe-cv-batch','--manifest-file')):
            with self.subTest(command=command), redirect_stderr(StringIO()):
                with self.assertRaises(SystemExit) as caught:
                    core.main([command,input_flag,'unused.json','--output-root',str(self.workspace),'--output-dir',str(self.workspace/'new')])
                self.assertEqual(caught.exception.code, 2)

    def test_resume_still_requires_the_exact_existing_directory(self):
        with redirect_stderr(StringIO()):
            with self.assertRaises(SystemExit):
                core.main(['resume','--request-file','unused.json'])
        with patch.object(core,'read',return_value={}), patch.object(core,'acquire',return_value={'status':'video_ready'}) as acquire:
            code, _ = self.cli(['resume','--request-file','unused.json','--output-dir',str(self.workspace)])
        self.assertEqual(code, 0)
        self.assertEqual(acquire.call_args.args[1], self.workspace)
        self.assertTrue(acquire.call_args.kwargs['resume'])

    def test_new_cli_entries_forward_the_resolved_destination_and_return_it(self):
        import batch_acquisition
        import serial_probe
        for command, input_flag, module, function in (
            ('acquire','--request-file',core,'acquire'),
            ('run-batch','--request-file',batch_acquisition,'run_batch'),
            ('probe-cv-batch','--manifest-file',serial_probe,'probe_batch'),
        ):
            with self.subTest(command=command), patch.object(Path,'cwd',return_value=self.workspace), patch.object(core,'read',return_value={}), patch.object(module,function,return_value={'status':'succeeded'}) as operation:
                code, result = self.cli([command,input_flag,'unused.json'])
                chosen = Path(result['runDir'])
                self.assertEqual(code, 0)
                self.assertEqual(chosen.parent, self.workspace/core.DEFAULT_OUTPUT_FOLDER)
                self.assertEqual(operation.call_args.args[1], chosen)
                exact = self.workspace/'exact'
                code, result = self.cli([command,input_flag,'unused.json','--output-dir',str(exact)])
                self.assertEqual(Path(result['runDir']), exact)
                code, result = self.cli([command,input_flag,'unused.json','--output-root',str(self.workspace/'custom')])
                self.assertEqual(Path(result['runDir']).parent, self.workspace/'custom')

    def test_failure_returns_chosen_run_for_diagnosis(self):
        with patch.object(core,'read',return_value={}), patch.object(core,'acquire',side_effect=core.ProbeError('fixture_failure')):
            code, result = self.cli(['acquire','--request-file','unused.json','--output-dir',str(self.workspace/'new')])
        self.assertEqual(code, 1)
        self.assertEqual(result['errorCode'], 'fixture_failure')
        self.assertEqual(result['runDir'], str(self.workspace/'new'))
