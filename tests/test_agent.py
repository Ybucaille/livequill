"""Boucle agent de bout en bout avec un faux fournisseur : outils, mode texte, annulation."""
import json, os, types, unittest

from helpers import FakeTransport, cleanup, temp_project

from livequill import agent
from livequill.config import Config
from livequill.net import HTTPError
from livequill.sandbox import Conversation, HistoryStore, Project

HTML = '<body>\n  <h1 class="hero-title">Cluster <em>overview</em></h1>\n</body>\n'


def ollama_lines(text='', calls=()):
    out = [json.dumps({'message': {'content': text}})] if text else []
    if calls:
        out.append(json.dumps({'message': {'content': '', 'tool_calls': [{'function': {'name': n, 'arguments': a}} for n, a in calls]}}))
    return out + [json.dumps({'done': True, 'done_reason': 'stop'})]


class AgentLoop(unittest.TestCase):
    def setUp(self):
        self.root = temp_project({'index.html': HTML})
        self.cfgdir = temp_project()
        self.config = Config(os.path.join(self.cfgdir, 'config.json'))
        self.config.upsert('ollama', {'type': 'ollama', 'models': [{'id': 'qwen', 'label': 'qwen', 'meta': {'capabilities': ['tools']}}],
                                      'default_model': 'qwen'})
        self.project = Project(self.root)

    def tearDown(self):
        cleanup(self.root, self.cfgdir)

    def ctx(self, streams):
        return types.SimpleNamespace(project=self.project, config=self.config, history=HistoryStore(self.project),
                                     conversation=Conversation(self.project), mode='static', transport=FakeTransport(streams))

    def job(self, text='passe le titre en rouge'):
        return agent.Job({'text': text, 'provider': 'ollama', 'model': 'qwen', 'page': {'pathname': '/', 'hash': ''},
                          'outline': 'h1.hero-title "Cluster overview"'})

    def html(self):
        with open(os.path.join(self.root, 'index.html')) as f:
            return f.read()

    def test_native_tools_edit_then_undo(self):
        ctx = self.ctx([
            ollama_lines(calls=[('search', {'pattern': 'Cluster overview'})]),
            ollama_lines(calls=[('edit_file', {'path': 'index.html', 'old_text': '<h1 class="hero-title">',
                                               'new_text': '<h1 class="hero-title" style="color:red">'})]),
            ollama_lines('Titre passé en rouge.')])
        job = self.job()
        answer, files = agent.run(ctx, job)
        self.assertEqual(answer, 'Titre passé en rouge.')
        self.assertEqual(files, ['index.html'])
        self.assertIn('style="color:red"', self.html())
        # le contexte envoyé contient la page, les titres visibles et la demande
        first = ctx.transport.requests[0]['body']['messages'][-1]['content']
        self.assertIn('Cluster overview', first)
        self.assertIn('served from the file: index.html', first)
        # le résultat de search est renvoyé au modèle
        self.assertIn('index.html:2:', ctx.transport.requests[1]['body']['messages'][-1]['content'])
        types_ = [e['type'] for e in job.events]
        self.assertIn('file', types_)
        ctx.history.undo()
        self.assertEqual(self.html(), HTML)

    def test_tool_errors_are_returned_not_raised(self):
        ctx = self.ctx([ollama_lines(calls=[('read_file', {'path': '../../etc/passwd'})]), ollama_lines('Impossible.')])
        answer, files = agent.run(ctx, self.job())
        self.assertEqual(files, [])
        self.assertIn('Error', ctx.transport.requests[1]['body']['messages'][-1]['content'])

    def test_falls_back_to_text_mode(self):
        # 1er appel refusé (pas de tools), puis protocole texte : bloc tool puis SEARCH/REPLACE
        ctx = self.ctx([
            HTTPError(400, '{"error": "model does not support tools"}'),
            ['{"message": {"content": "```tool\\n{\\"name\\": \\"search\\", \\"args\\": {\\"pattern\\": \\"hero-title\\"}}\\n```"}}', '{"done": true}'],
            [json.dumps({'message': {'content': 'index.html\n<<<<<<< SEARCH\n<em>overview</em>\n=======\n<em style="color:green">overview</em>\n>>>>>>> REPLACE\nFait.'}}),
             '{"done": true}']])
        answer, files = agent.run(ctx, self.job())
        self.assertIn('color:green', self.html())
        self.assertEqual(files, ['index.html'])
        self.assertEqual(answer, 'Fait.')
        self.assertNotIn('tools', ctx.transport.requests[1]['body'])
        self.assertIn('text protocol', ctx.transport.requests[1]['body']['messages'][0]['content'])

    def test_tool_call_written_as_text_is_executed(self):
        leaked = 'portun\n{"name": "edit_file", "arguments": {"path": "index.html", "old_text": "Cluster", "new_text": "Swarm"}}\n</tool_call>'
        ctx = self.ctx([ollama_lines(leaked), ollama_lines('Renommé.')])
        answer, files = agent.run(ctx, self.job('renomme Cluster en Swarm'))
        self.assertIn('Swarm <em>', self.html())
        self.assertEqual(answer, 'Renommé.')

    def test_nudges_once_when_model_stops_silently(self):
        ctx = self.ctx([ollama_lines(), ollama_lines(calls=[('edit_file', {'path': 'index.html', 'old_text': 'Cluster', 'new_text': 'Swarm'})]),
                        ollama_lines('ok')])
        answer, files = agent.run(ctx, self.job())
        self.assertEqual(files, ['index.html'])
        self.assertEqual(len(ctx.transport.requests), 3)

    def test_history_is_text_only_and_marks_undone(self):
        turns = [{'prompt': 'avant', 'answer': 'fait', 'files': ['a.css'], 'status': 'undone', 'page': '/'},
                 {'prompt': 'raté', 'answer': 'boom', 'status': 'error'}]
        msgs = agent.history_messages(turns)
        self.assertEqual(len(msgs), 2)
        self.assertIn('UNDID', msgs[1]['content'][0]['text'])

    def test_cancel(self):
        job = self.job()
        job.cancel()
        with self.assertRaises(agent.Cancelled):
            agent.run(self.ctx([ollama_lines('x')]), job)


if __name__ == '__main__':
    unittest.main()
