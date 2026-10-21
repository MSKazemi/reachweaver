// Facts shared by the Astro config and the structured data. Only what the repository itself states.
import meta from './generated/site-meta.json' with { type: 'json' };

export const SITE_URL = 'https://mskazemi.com/reachweaver';
export const GITHUB = 'https://github.com/MSKazemi/reachweaver';
export const PYPI = 'https://pypi.org/project/reachweaver/';
export const VERSION = meta.version;
export const DESCRIPTION =
	'ReachWeaver is an open-source Claude Code skill and Python CLI for approval-first social publishing: plan, draft, check and preview posts, and send only the exact text you approve.';
export const AUTHOR = {
	name: 'Mohsen Seyedkazemi Ardebili',
	url: 'https://mskazemi.com/',
	sameAs: ['https://github.com/MSKazemi', 'https://gitlab.com/mskazemi'],
};
