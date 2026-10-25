import type {ExplanationOutput} from './contracts.js';
type Example={id:string;tags:string[];question:string;context:string;output:ExplanationOutput};
const answer=(items:[string,string,string[]][],note='Please confirm current rent and availability with the agent.'):ExplanationOutput=>({version:'2.0',focus_listing_ids:items.map(x=>x[0]),paragraphs:[
  {listing_id:null,text:'These details may help you compare the homes:',support_refs:['guide:purpose']},
  ...items.map(([listing_id,text,support_refs])=>({listing_id,text,support_refs})),
  {listing_id:null,text:note,support_refs:['availability:current']},
]});
export const EXPLANATION_EXAMPLES:Example[]=[
  {
    "id": "whole-list-outlier",
    "tags": [
      "lowest",
      "cheapest",
      "highest",
      "in the middle",
      "Top5"
    ],
    "question": "What stands out about these five homes?",
    "context": "Teaching example: five verified recommendations. Rents: 1500/2300/2400/2600/2700 SGD; listed areas: 400/600/1000/500/700 sq ft. Home 1 has clearly lowest rent; 3 has largest area; 5 has highest rent; 2 and 5 have middle areas. Home 1: Fresh Mart 360 m straight-line; 2: two bathrooms; 3: Lake MRT 600 m property-reported; 4: two bedrooms; 5: Hill Stop bus stop 200 m straight-line. refs rent:1..5,area:1..5,cohort:rent:1..5,cohort:area:1..5,shop:1,bath:2,mrt:3,beds:4,bus:5.",
    "output": {
      "version": "2.0",
      "focus_listing_ids": [
        "example-a",
        "example-b",
        "example-c",
        "example-d",
        "example-e"
      ],
      "paragraphs": [
        {
          "listing_id": null,
          "text": "These details may help you compare the homes:",
          "support_refs": [
            "guide:purpose"
          ]
        },
        {
          "listing_id": "example-a",
          "text": "1. Pine: At S$1,500 per month, this has the lowest rent among the five, leaving more of your budget for other needs. Its 400 sq ft listed area is also on the smaller side; Fresh Mart is about 360 metres away in a straight line, offering another grocery option.",
          "support_refs": [
            "rent:1",
            "area:1",
            "cohort:rent:1",
            "cohort:area:1",
            "shop:1"
          ]
        },
        {
          "listing_id": "example-b",
          "text": "2. Grove: Its two bathrooms are worth considering if household members keep different schedules. The listed area of 600 sq ft is in the middle here, while monthly rent of S$2,300 is on the lower side.",
          "support_refs": [
            "bath:2",
            "area:2",
            "rent:2",
            "cohort:area:2",
            "cohort:rent:2"
          ]
        },
        {
          "listing_id": "example-c",
          "text": "3. Lake: At 1,000 sq ft, this has the largest listed area, worth a closer look if space matters to you. Its S$2,400 monthly rent is in the middle; nearby Lake MRT is about 600 metres away according to the property information, useful when considering transport options.",
          "support_refs": [
            "area:3",
            "rent:3",
            "cohort:area:3",
            "cohort:rent:3",
            "mrt:3"
          ]
        },
        {
          "listing_id": "example-d",
          "text": "4. Elm: Two bedrooms give you another layout to compare. Its 500 sq ft listed area is on the smaller side here and S$2,600 monthly rent is on the higher side, so consider whether the space is enough for you.",
          "support_refs": [
            "beds:4",
            "area:4",
            "rent:4",
            "cohort:area:4",
            "cohort:rent:4"
          ]
        },
        {
          "listing_id": "example-e",
          "text": "5. Hill: Hill Stop bus stop is about 200 metres away in a straight line, giving you another transport option. S$2,700 is the highest monthly rent here, with a middle-sized listed area of 700 sq ft; consider whether that location warrants the budget.",
          "support_refs": [
            "bus:5",
            "rent:5",
            "area:5",
            "cohort:rent:5",
            "cohort:area:5"
          ]
        },
        {
          "listing_id": null,
          "text": "Please confirm current rent and availability with the agent.",
          "support_refs": [
            "availability:current"
          ]
        }
      ]
    }
  },
  {
    "id": "whole-list-distance",
    "tags": [
      "why",
      "homes",
      "recommended",
      "reasons",
      "how to choose"
    ],
    "question": "How should I choose between these homes?",
    "context": "Home 1 Orchard Home: rent2200, listed area700sqft, Fresh Mart250m straight-line. Home2 Lake Home: rent2400, listed area900sqft, Lake MRT600m property-reported. User only requested budget2600; area and amenities are additional known facts. Do not change ranking. refs rent:1,area:1,market:1,rent:2,area:2,mrt:2,price-diff,area-diff.",
    "output": {
      "version": "2.0",
      "focus_listing_ids": [
        "example-a",
        "example-b"
      ],
      "paragraphs": [
        {
          "listing_id": null,
          "text": "These details may help you compare the homes:",
          "support_refs": [
            "guide:purpose"
          ]
        },
        {
          "listing_id": "example-a",
          "text": "1. Orchard Home: Its S$2,200 monthly rent is the lower of the two, with a listed area of 700 sq ft. If controlling rent comes first, this is worth a look; Fresh Mart is about 250 metres away in a straight line, offering another grocery option.",
          "support_refs": [
            "rent:1",
            "area:1",
            "price-diff",
            "market:1"
          ]
        },
        {
          "listing_id": "example-b",
          "text": "2. Lake Home: Its listed area of 900 sq ft is the larger option here, at S$2,400 per month. The property information places Lake MRT about 600 metres away, useful when considering public transport; check the actual walking route.",
          "support_refs": [
            "rent:2",
            "area:2",
            "area-diff",
            "mrt:2"
          ]
        },
        {
          "listing_id": null,
          "text": "Please confirm current rent and availability with the agent.",
          "support_refs": [
            "availability:current"
          ]
        }
      ]
    }
  },
  {
    "id": "bathroom-count-not-privacy",
    "tags": [
      "1B1B",
      "bathrooms",
      "private bathroom"
    ],
    "question": "Introduce these two homes.",
    "context": "Home1 Maple Home: rent1800, listing records one bathroom but Recommendation has not verified the user’s one-bathroom hard requirement. Home2 Cedar Home: rent1900, area650sqft. No exclusive-use evidence. refs rent:1,bath:1,unverified:1,area:2.",
    "output": {
      "version": "2.0",
      "focus_listing_ids": [
        "example-a",
        "example-b"
      ],
      "paragraphs": [
        {
          "listing_id": null,
          "text": "These details may help you compare the homes:",
          "support_refs": [
            "guide:purpose"
          ]
        },
        {
          "listing_id": "example-a",
          "text": "1. Maple Home: The recorded monthly rent of S$1,800 is an option if you want to control rent. The listing mentions one bathroom, but this requirement is unverified in the current recommendation and needs checking with the agent; the count alone does not establish private use.",
          "support_refs": [
            "rent:1",
            "bath:1",
            "unverified:1"
          ]
        },
        {
          "listing_id": "example-b",
          "text": "2. Cedar Home: Its listed area is 650 sq ft, which may help when comparing layouts and rental scope; this does not establish the area of an individual bedroom.",
          "support_refs": [
            "area:2"
          ]
        },
        {
          "listing_id": null,
          "text": "Please confirm current rent and availability with the agent.",
          "support_refs": [
            "availability:current"
          ]
        }
      ]
    }
  },
  {
    "id": "mixed-confirmed-pending",
    "tags": [
      "information",
      "Unknown",
      "group"
    ],
    "question": "What are the features of these homes?",
    "context": "Home1 Grove: rent2000, two bedrooms, Local Mart300m straight-line, budget and bedrooms verified. Home2 Meadow: rent1800, bedrooms unknown, River Park500m straight-line, outside verified ranking. refs rent:1,market:1,rent:2,park:2,beds:unknown.",
    "output": {
      "version": "2.0",
      "focus_listing_ids": [
        "example-a",
        "example-b"
      ],
      "paragraphs": [
        {
          "listing_id": null,
          "text": "These details may help you compare the homes:",
          "support_refs": [
            "guide:purpose"
          ]
        },
        {
          "listing_id": "example-a",
          "text": "1. Grove: Its recorded two bedrooms and S$2,000 monthly rent match your requirements. Local Mart is also about 300 metres away in a straight line, offering another everyday shopping option.",
          "support_refs": [
            "rent:1",
            "market:1"
          ]
        },
        {
          "listing_id": "example-b",
          "text": "2. Meadow: Recorded monthly rent is S$1,800, with River Park about 500 metres away in a straight line as an outdoor option. Bedroom count is still unknown, so the requested layout cannot yet be confirmed.",
          "support_refs": [
            "rent:2",
            "park:2",
            "beds:unknown"
          ]
        },
        {
          "listing_id": null,
          "text": "Please confirm current rent and availability with the agent.",
          "support_refs": [
            "availability:current"
          ]
        }
      ]
    }
  },
  {
    "id": "stable-order",
    "tags": [
      "no preferences",
      "all match",
      "differences"
    ],
    "question": "They all fit my budget. How should I choose?",
    "context": "Stable order is not rent ranking. Home1 Green: rent1800, area500sqft. Home2 Blue: rent2000, area700sqft. Area is additional information; do not claim overall best. refs area:1,area:2,rent:1,rent:2,price-diff,area-diff.",
    "output": {
      "version": "2.0",
      "focus_listing_ids": [
        "example-a",
        "example-b"
      ],
      "paragraphs": [
        {
          "listing_id": null,
          "text": "These details may help you compare the homes:",
          "support_refs": [
            "guide:purpose"
          ]
        },
        {
          "listing_id": "example-a",
          "text": "1. Green: S$1,800 per month leaves more budget than the other option. The listed area is 500 sq ft and relatively smaller, so check whether the layout gives you enough space.",
          "support_refs": [
            "rent:1",
            "price-diff",
            "area:1"
          ]
        },
        {
          "listing_id": "example-b",
          "text": "2. Blue: Its 700 sq ft listed area is the larger of the two, with higher rent of S$2,000 per month. If space matters more, consider it alongside the actual rental scope.",
          "support_refs": [
            "area:2",
            "area-diff",
            "rent:2"
          ]
        },
        {
          "listing_id": null,
          "text": "Please confirm current rent and availability with the agent.",
          "support_refs": [
            "availability:current"
          ]
        }
      ]
    }
  },
  {
    "id": "distance-tradeoff-with-unranked",
    "tags": [
      "closer is better",
      "School",
      "Supermarket",
      "one room"
    ],
    "question": "Make it closer to the school, with one bedroom.",
    "context": "Home1 Grove: school2500m straight-line, Local Mart60m. Home2 Elm: school2300m straight-line, Fresh Mart140m. Combined contributions place Grove first; do not change the order. Budget is only a filter. refs school:1,market:1,school:2,market:2,school-diff.",
    "output": {
      "version": "2.0",
      "focus_listing_ids": [
        "example-a",
        "example-b"
      ],
      "paragraphs": [
        {
          "listing_id": null,
          "text": "These details may help you compare the homes:",
          "support_refs": [
            "guide:purpose"
          ]
        },
        {
          "listing_id": "example-a",
          "text": "1. Grove: Local Mart is about 60 metres away in a straight line, a useful shopping feature to consider. The school is about 2.5 km away in a straight line, so weigh that against the actual route.",
          "support_refs": [
            "school:1",
            "market:1"
          ]
        },
        {
          "listing_id": "example-b",
          "text": "2. Elm: At about 2.3 km in a straight line, it is closer to the school than Grove. Fresh Mart is about 140 metres away in a straight line, another shopping option. If school distance matters more, compare this home closely.",
          "support_refs": [
            "school:2",
            "school-diff",
            "market:2"
          ]
        },
        {
          "listing_id": null,
          "text": "Please confirm current rent and availability with the agent.",
          "support_refs": [
            "availability:current"
          ]
        }
      ]
    }
  },
  {
    "id": "multiple-preferences",
    "tags": [
      "price",
      "cheaper",
      "Floor area",
      "expensive"
    ],
    "question": "Why is the first home not necessarily the cheapest?",
    "context": "User explicitly asks about ranking. Home1 Garden: rent1700, school1600m, area700sqft. Home2 Maple: rent2000, school900m, area800sqft. Distance and soft rent preference jointly rank Garden first. refs rent:1,area:1,school:1,rent:2,area:2,school:2,ranking:method.",
    "output": {
      "version": "2.0",
      "focus_listing_ids": [
        "example-a",
        "example-b"
      ],
      "paragraphs": [
        {
          "listing_id": null,
          "text": "These details may help you compare the homes:",
          "support_refs": [
            "guide:purpose"
          ]
        },
        {
          "listing_id": "example-a",
          "text": "1. Garden: Recorded monthly rent is S$1,700 with a listed area of 700 sq ft, about 1.6 km from the school in a straight line. The lower rent makes this worth comparing if budget headroom matters to you.",
          "support_refs": [
            "rent:1",
            "area:1",
            "school:1"
          ]
        },
        {
          "listing_id": "example-b",
          "text": "2. Maple: Its school distance is about 900 metres in a straight line, with an 800 sq ft listed area and recorded monthly rent of S$2,000. The school distance is more attractive, though the rent is higher. The original order combines rent preference and school distance rather than using either alone.",
          "support_refs": [
            "school:2",
            "area:2",
            "rent:2",
            "ranking:method"
          ]
        },
        {
          "listing_id": null,
          "text": "Please confirm current rent and availability with the agent.",
          "support_refs": [
            "availability:current"
          ]
        }
      ]
    }
  },
  {
    "id": "multiple-preferences-english",
    "tags": [
      "why",
      "choose",
      "compare",
      "English"
    ],
    "question": "How should I choose between these homes?",
    "context": "1 Maple recorded rent2200,listing area700sqft,Fresh Mart250m straight-line;2 Lake rent2400,listing area900sqft,Lake MRT600m platform reported. Area/transit not requested. refs price-diff,market:1,area:2,mrt:2.",
    "output": {
      "version": "2.0",
      "focus_listing_ids": [
        "example-a",
        "example-b"
      ],
      "paragraphs": [
        {
          "listing_id": null,
          "text": "These details may help you compare the homes:",
          "support_refs": [
            "guide:purpose"
          ]
        },
        {
          "listing_id": "example-a",
          "text": "1. Maple: At S$2,200 per month, this is the lower-rent option, with a listed area of 700 sq ft. Fresh Mart is about 250 metres away in a straight line, giving you another option for groceries.",
          "support_refs": [
            "rent:1",
            "area:1",
            "price-diff",
            "market:1"
          ]
        },
        {
          "listing_id": "example-b",
          "text": "2. Lake: Its listed area of 900 sq ft offers the larger option here, at S$2,400 per month. Nearby Lake MRT is shown at about 600 metres in the property information, useful when considering public transport; check the actual route.",
          "support_refs": [
            "rent:2",
            "area:2",
            "mrt:2"
          ]
        },
        {
          "listing_id": null,
          "text": "Please confirm current rent, availability and actual routes with the agent.",
          "support_refs": [
            "availability:current"
          ]
        }
      ]
    }
  },
  {
    "id": "empty-results",
    "tags": [
      "no homes",
      "empty"
    ],
    "question": "Are there no homes for this search?",
    "context": "No displayable homes for this request; do not infer none exist in the entire market or change conditions automatically. refs results:empty.",
    "output": {
      "version": "2.0",
      "focus_listing_ids": [],
      "paragraphs": [
        {
          "listing_id": null,
          "text": "No homes can currently be displayed for these conditions. Would you consider changing the budget, search area or layout requirement first?",
          "support_refs": [
            "results:empty"
          ]
        }
      ]
    }
  },
  {
    "id": "behavior-ranking",
    "tags": [
      "viewing",
      "interests"
    ],
    "question": "What advantages do these homes offer?",
    "context": "Property-type/area viewing signals contributed to the original ranking. Introduce known features without inventing explicit user preferences. Home1 Pine: area800sqft. Home2 Oak: rent2100, Corner Mart200m straight-line. refs area:1,rent:2,market:2.",
    "output": {
      "version": "2.0",
      "focus_listing_ids": [
        "example-a",
        "example-b"
      ],
      "paragraphs": [
        {
          "listing_id": null,
          "text": "These details may help you compare the homes:",
          "support_refs": [
            "guide:purpose"
          ]
        },
        {
          "listing_id": "example-a",
          "text": "1. Pine: Its listed area of 800 sq ft offers a reference point when comparing layouts; confirm the actual rental scope.",
          "support_refs": [
            "area:1"
          ]
        },
        {
          "listing_id": "example-b",
          "text": "2. Oak: Recorded monthly rent is S$2,100, with Corner Mart about 200 metres away in a straight line, giving you another everyday shopping option.",
          "support_refs": [
            "rent:2",
            "market:2"
          ]
        },
        {
          "listing_id": null,
          "text": "Please confirm current rent and availability with the agent.",
          "support_refs": [
            "availability:current"
          ]
        }
      ]
    }
  },
  {
    "id": "similar-layout-price-choice",
    "tags": [
      "compare two",
      "difference",
      "per sq ft",
      "PSF",
      "value"
    ],
    "question": "Compare these two. Is the space worth the rent?",
    "context": "Teaching example: A/B are whole-unit rentals with the same area basis and one bedroom/one bathroom. A:rent2500,area500sqft; B:rent2600,area520sqft. Similar space/layout allows a 100 monthly difference when explicitly comparing. Both unit rents are5SGD/sqft/month; do not claim overall better value. refs rent:a,area:a,rent:b,area:b,pair:rent,unit:a,unit:b.",
    "output": {
      "version": "2.0",
      "focus_listing_ids": [
        "example-a",
        "example-b"
      ],
      "paragraphs": [
        {
          "listing_id": null,
          "text": "These details may help you compare the homes:",
          "support_refs": [
            "guide:purpose"
          ]
        },
        {
          "listing_id": "example-a",
          "text": "1. A: Monthly rent is S$2,500 for a listed area of 500 sq ft. The layouts and sizes are close, and this home costs S$100 less per month, so it is worth a look if total rent is your priority.",
          "support_refs": [
            "rent:a",
            "area:a",
            "pair:rent"
          ]
        },
        {
          "listing_id": "example-b",
          "text": "2. B: The listed area is 520 sq ft at S$2,600 per month. You get slightly more space for slightly higher rent; compare the location and actual layout before choosing.",
          "support_refs": [
            "rent:b",
            "area:b"
          ]
        },
        {
          "listing_id": null,
          "text": "Please confirm current rent and availability with the agent.",
          "support_refs": [
            "availability:current"
          ]
        }
      ]
    }
  },
  {
    "id": "unit-rent-meaningful-comparison",
    "tags": [
      "per sq ft",
      "PSF",
      "unit area",
      "value"
    ],
    "question": "How do these five compare for space and rent?",
    "context": "Teaching example: all five are explicitly whole-unit rentals with the same area basis. Unit rents6/4/4.2/4.3/2.4SGD/sqft/month. A:rent3000,area500; B:4000/1000; C:2100/500; D:2580/600; E:2400/1000. A is clearly highest and E clearly lowest. B/C/D are middle and have no unit-rent highlight. refs rent:a..e,area:a..e,cohort:unit:a,cohort:unit:e.",
    "output": {
      "version": "2.0",
      "focus_listing_ids": [
        "example-a",
        "example-b",
        "example-c",
        "example-d",
        "example-e"
      ],
      "paragraphs": [
        {
          "listing_id": null,
          "text": "These details may help you compare the homes:",
          "support_refs": [
            "guide:purpose"
          ]
        },
        {
          "listing_id": "example-a",
          "text": "1. A: Rent is S$3,000 per month for 500 sq ft. At about S$6 per sq ft per month, this is the highest among the five: the same space costs more. Considering space alone, it offers less value, so weigh the other features before deciding.",
          "support_refs": [
            "rent:a",
            "area:a",
            "cohort:unit:a"
          ]
        },
        {
          "listing_id": "example-b",
          "text": "2. B: Its listed area is 1,000 sq ft at S$4,000 per month. It offers more space but needs more budget; check whether the actual layout makes that space useful to you.",
          "support_refs": [
            "rent:b",
            "area:b"
          ]
        },
        {
          "listing_id": "example-c",
          "text": "3. C: Monthly rent is S$2,100 with a listed area of 500 sq ft. The total rent is easier to control, making it worth considering if this space is enough.",
          "support_refs": [
            "rent:c",
            "area:c"
          ]
        },
        {
          "listing_id": "example-d",
          "text": "4. D: At 600 sq ft and S$2,580 per month, this offers another balance of space and total rent. Location can help distinguish it next.",
          "support_refs": [
            "rent:d",
            "area:d"
          ]
        },
        {
          "listing_id": "example-e",
          "text": "5. E: Its listed area is 1,000 sq ft at S$2,400 per month, about S$2.40 per sq ft per month, the lowest among these five. Considering space and rent alone, it offers better value if you want more of your budget to go towards area.",
          "support_refs": [
            "rent:e",
            "area:e",
            "cohort:unit:e"
          ]
        },
        {
          "listing_id": null,
          "text": "Please confirm current rent and availability with the agent.",
          "support_refs": [
            "availability:current"
          ]
        }
      ]
    }
  }
];
const genericTags=new Set(['why','homes',"recommended",'reasons']);
export function selectExplanationExamples(query:string,count=2,contextId?:string){return EXPLANATION_EXAMPLES.map((e,i)=>({e,i,score:(e.id===contextId?100:0)+e.tags.reduce((n,t)=>n+(query.toLowerCase().includes(t.toLowerCase())?(genericTags.has(t)?1:4+Math.min(t.length,12)/12):0),0)})).sort((a,b)=>b.score-a.score||a.i-b.i).slice(0,count).map(x=>x.e);}
