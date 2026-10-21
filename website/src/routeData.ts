// Structured data (JSON-LD) for every page. It states only what the page and the repository show: the software, its
// author, the page itself and where it sits in the site. No ratings, reviews or counts are claimed.
import { defineRouteMiddleware } from '@astrojs/starlight/route-data';
import sidebar from './generated/sidebar.json';
import { AUTHOR, DESCRIPTION, GITHUB, PYPI, SITE_URL, VERSION } from './site.mjs';

const HOME = `${SITE_URL}/`;
const PERSON_ID = `${AUTHOR.url}#person`;
const SOFTWARE_ID = `${HOME}#software`;
const WEBSITE_ID = `${HOME}#website`;
// Each top-level sidebar group, keyed by its folder: the label and the URL of its first page (a breadcrumb item
// needs a URL, and the section has no index page of its own).
const SECTIONS: Record<string, { name: string; item: string }> = Object.fromEntries(
	(sidebar as { label: string; items: { slug?: string }[] }[])
		.map((group) => {
			const first = group.items.find((item) => item.slug)?.slug;
			return [first?.split('/')[0], { name: group.label, item: `${SITE_URL}/${first}/` }];
		})
		.filter(([folder]) => folder),
);

const person = {
	'@type': 'Person',
	'@id': PERSON_ID,
	name: AUTHOR.name,
	url: AUTHOR.url,
	sameAs: AUTHOR.sameAs,
};
const website = {
	'@type': 'WebSite',
	'@id': WEBSITE_ID,
	url: HOME,
	name: 'ReachWeaver documentation',
	alternateName: 'ReachWeaver',
	description: DESCRIPTION,
	inLanguage: 'en',
	publisher: { '@id': PERSON_ID },
};
const software = {
	'@type': ['SoftwareApplication', 'SoftwareSourceCode'],
	'@id': SOFTWARE_ID,
	name: 'ReachWeaver',
	description: DESCRIPTION,
	url: HOME,
	applicationCategory: 'DeveloperApplication',
	applicationSubCategory: 'Social media publishing',
	operatingSystem: 'Linux, macOS, Windows',
	softwareVersion: VERSION,
	softwareRequirements: 'Python 3.11 or newer',
	license: 'https://www.apache.org/licenses/LICENSE-2.0',
	codeRepository: GITHUB,
	programmingLanguage: 'Python',
	downloadUrl: PYPI,
	isAccessibleForFree: true,
	offers: { '@type': 'Offer', price: '0', priceCurrency: 'USD' },
	author: { '@id': PERSON_ID },
	sameAs: [GITHUB, PYPI],
};

function jsonLd(graph: object[]) {
	// "<" escaped so no page text can close the script element.
	return JSON.stringify({ '@context': 'https://schema.org', '@graph': graph }).replace(/</g, '\\u003c');
}

export const onRequest = defineRouteMiddleware((context) => {
	const route = context.locals.starlightRoute;
	const { entry, head } = route;
	const url = new URL(context.url.pathname, context.site).href;
	const title = entry.data.title;
	const description = entry.data.description ?? DESCRIPTION;
	const isHome = entry.id === 'index' || entry.id === '';
	const graph: object[] = [person, website];

	if (isHome) {
		graph.push(software);
		const ogType = head.find((tag) => tag.attrs?.property === 'og:type');
		if (ogType?.attrs) ogType.attrs.content = 'website';
	} else {
		const folder = entry.id.split('/')[0];
		const crumbs = [{ name: 'Docs', item: HOME }];
		const section = SECTIONS[folder];
		if (section && entry.id.includes('/') && section.item !== url) crumbs.push(section);
		crumbs.push({ name: title, item: url });
		graph.push({
			'@type': 'BreadcrumbList',
			itemListElement: crumbs.map((crumb, index) => ({
				'@type': 'ListItem',
				position: index + 1,
				name: crumb.name,
				item: crumb.item,
			})),
		});
		const modified = entry.data.lastUpdated instanceof Date ? entry.data.lastUpdated.toISOString() : undefined;
		graph.push({
			'@type': 'TechArticle',
			'@id': `${url}#article`,
			headline: title,
			description,
			url,
			mainEntityOfPage: url,
			inLanguage: 'en',
			...(modified ? { dateModified: modified } : {}),
			author: { '@id': PERSON_ID },
			publisher: { '@id': PERSON_ID },
			isPartOf: { '@id': WEBSITE_ID },
			about: { '@id': SOFTWARE_ID },
			image: `${SITE_URL}/og.png`,
		});
		if (modified) head.push({ tag: 'meta', attrs: { property: 'article:modified_time', content: modified } });
	}
	head.push({ tag: 'script', attrs: { type: 'application/ld+json' }, content: jsonLd(graph) });
});
