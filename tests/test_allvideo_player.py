
from modules.sources.players.allvideo import read_playerjs

# The shapes below are the ones AllVideo was seen to answer: the player with its file list, and the placeholder
# that comes with status 404 for an embed that does not exist.
PLAYER = ('<html><head><title>Серия 1 4K</title></head><body><script>new Playerjs({ id: "v", '
          'file:"[360p]https://h/360.mp4/,[720p]https://h/720.mp4/,[1080p]https://h/1080.mp4/" });</script></body></html>')
NO_FILE = "<html><head><title>Серия 2</title></head><body><script>new Playerjs({ id: 'v' })</script></body></html>"
PLACEHOLDER = "<html><head><title>404</title></head><script>new Playerjs({ file: '/contents/other/video_error.mp4' })</script></html>"
def test_a_player_that_is_not_playable_has_no_streams():
    assert read_playerjs(200, NO_FILE).files == () and read_playerjs(404, PLACEHOLDER).files == ()
