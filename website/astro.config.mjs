// ReachWeaver documentation website. Content in src/content/docs is generated from the repository's public Markdown
// by scripts/build_docs_site.py (run it before `npm run build`); only index.mdx, faq.mdx and 404.md are written by hand.
import { defineConfig } from 'astro/config';
import starlight from '@astrojs/starlight';
import sitemap from '@astrojs/sitemap';
import starlightLinksValidator from 'starlight-links-validator';
import lastmod from './src/generated/lastmod.json' with { type: 'json' };
import sidebar from './src/generated/sidebar.json' with { type: 'json' };
import { AUTHOR, DESCRIPTION, GITHUB, SITE_URL } from './src/site.mjs';

export default defineConfig({
	site: 'https://mskazemi.com', // GitHub Pages custom domain; mskazemi.github.io 301-redirects here
	base: '/reachweaver',
	trailingSlash: 'always',
	build: { format: 'directory' },
	integrations: [
		starlight({
			title: 'ReachWeaver',
			description: DESCRIPTION,
			logo: { src: './src/assets/logo.webp', alt: 'ReachWeaver' },
			favicon: '/favicon.png',
			social: [{ icon: 'github', label: 'ReachWeaver on GitHub', href: GITHUB }],
			editLink: { baseUrl: `${GITHUB}/edit/main/` },
			credits: false,
			tableOfContents: { minHeadingLevel: 2, maxHeadingLevel: 3 },
			customCss: ['@fontsource-variable/bricolage-grotesque', './src/styles/theme.css'],
			components: { Hero: './src/components/Hero.astro', Footer: './src/components/Footer.astro' },
			routeMiddleware: './src/routeData.ts',
			plugins: [starlightLinksValidator({ errorOnLocalLinks: false, errorOnInvalidHashes: true })],
			head: [
				{ tag: 'meta', attrs: { name: 'author', content: AUTHOR.name } },
				{ tag: 'meta', attrs: { name: 'theme-color', content: '#0a1522' } },
				{ tag: 'meta', attrs: { property: 'og:image', content: `${SITE_URL}/og.png` } },
				{ tag: 'meta', attrs: { property: 'og:image:width', content: '1200' } },
				{ tag: 'meta', attrs: { property: 'og:image:height', content: '630' } },
				{ tag: 'meta', attrs: { property: 'og:image:alt', content: 'ReachWeaver: approval-first social publishing for Claude Code' } },
				{ tag: 'meta', attrs: { name: 'twitter:image', content: `${SITE_URL}/og.png` } },
				{ tag: 'link', attrs: { rel: 'apple-touch-icon', href: '/reachweaver/apple-touch-icon.png' } },
				{ tag: 'link', attrs: { rel: 'manifest', href: '/reachweaver/site.webmanifest' } },
				{ tag: 'link', attrs: { rel: 'alternate', type: 'text/plain', title: 'llms.txt', href: '/reachweaver/llms.txt' } },
			],
			sidebar: [{ label: 'Home', link: '/' }, { slug: 'faq' }, ...sidebar],
		}),
		// Declared here (Starlight then skips its own) to add <lastmod>, the last commit date of each page's source.
		sitemap({
			serialize(item) {
				const route = item.url.slice(`${SITE_URL}/`.length).replace(/\/$/, '');
				if (lastmod[route]) item.lastmod = lastmod[route];
				return item;
			},
		}),
	],
});
