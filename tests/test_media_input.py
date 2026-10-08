import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import probe_core as core

class MediaInputTests(unittest.TestCase):
    def media(self,width=1080,height=1922,**extra):
        return {'width':width,'height':height,'durationSec':54.636,'fps':30,**extra}

    def test_geometry_boundaries_and_pixel_ceiling(self):
        for width,height,allowed in ((1080,1920,True),(1080,1922,True),(1922,1080,True),
                                     (1080,1936,True),(1080,1937,False),(1920,1920,True),
                                     (1936,1936,False),(3840,2160,False),(0,1080,None),(-1,1080,None)):
            with self.subTest(width=width,height=height):
                if allowed is None:
                    with self.assertRaises(core.ProbeError):core.media_input_admission(self.media(width,height))
                else:
                    admission=core.media_input_admission(self.media(width,height))
                    self.assertEqual(admission['status']=='admitted',allowed)
                    if (width,height)==(1936,1936):
                        self.assertEqual([v['field'] for v in admission['violations']],['pixelCount'])

    def test_duration_and_fps_limits_remain(self):
        for extra,allowed in (({'durationSec':180,'fps':60},True),({'durationSec':180.01},False),
                              ({'fps':60.01},False),({'durationSec':0},False),({'fps':0},False)):
            with self.subTest(extra=extra):
                self.assertEqual(core.media_input_admission(self.media(**extra))['status']=='admitted',allowed)
        for extra in ({'width':True},{'fps':float('nan')},{'durationSec':float('inf')}):
            with self.subTest(extra=extra),self.assertRaises(core.ProbeError):core.media_input_admission(self.media(**extra))

    def test_config_cannot_expand_beyond_reviewed_bounds(self):
        original=core.read(core.ROOT/'config/media-input-policy.json')
        for extra in ({'maxDimension':1937},{'maxPixels':1920*1920+1},{'maxDimension':True},{'maxPixels':0}):
            with self.subTest(extra=extra),patch.object(core,'read',return_value={**original,**extra}):
                with self.assertRaises(core.ProbeError) as error:core.media_input_admission(self.media())
                self.assertEqual(error.exception.code,'media_input_policy_invalid')

    def test_ffprobe_and_header_providers_apply_same_tolerance(self):
        for height in (1922,1938):
            raw={'streams':[{'codec_type':'video','codec_name':'h264','width':1080,'height':height,'avg_frame_rate':'30/1'}],
                 'format':{'duration':'54.636'}}
            for provider in ('ffprobe','ffmpeg_header'):
                response=type('Response',(),{'returncode':0,'stdout':json.dumps(raw),
                          'stderr':f'Duration: 00:00:54.63\nVideo: h264, yuv420p, 1080x{height}, 30 fps'})()
                with self.subTest(height=height,provider=provider),patch.object(core.shutil,'which',return_value='/fixture/ffprobe' if provider=='ffprobe' else None),patch.object(core.subprocess,'run',return_value=response),patch.object(core,'digest',return_value='fixture-binary-sha'):
                    if height==1922:
                        info=core.probe_media(Path('fixture.mp4'))
                        self.assertEqual(info['height'],1922);self.assertEqual(info['probeProvider'],provider)
                    else:
                        with self.assertRaises(core.ProbeError) as error:core.probe_media(Path('fixture.mp4'))
                        self.assertEqual(error.exception.code,'media_input_limit')
                        self.assertEqual(error.exception.media['height'],1938)
                        self.assertIn('1936',str(error.exception))

    def test_resource_tolerance_does_not_relax_identity_comparison(self):
        actual=self.media()
        self.assertEqual(core.media_input_admission(actual)['status'],'admitted')
        self.assertEqual(core.compare_media({'width':1080,'height':1920},actual)['status'],'mismatch')
