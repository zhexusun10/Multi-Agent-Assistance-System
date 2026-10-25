import type {ModelIntake} from './contracts.js';
import type {Requirements} from '../../contracts/recommendation-v1.js';
export type IntakeExample={id:string;query:string;base:Requirements;output:ModelIntake};
const empty:Requirements={listing_type:'RENT',criteria:[]};
const multiTurnBase:Requirements={listing_type:'RENT',criteria:[
  {criterion_id:'minimum-bedrooms',field:'bedrooms',operator:'eq',value:1,unit:'count',strength:'hard'},
  {criterion_id:'bathrooms',field:'bathrooms',operator:'eq',value:1,unit:'count',strength:'hard'},
  {criterion_id:'monthly-rent',field:'price',operator:'lte',value:2400,unit:'SGD/month',strength:'hard'},
  {criterion_id:'place_proximity:URA:AREA:CLEMENTI',field:'place_proximity',operator:'lte',value:4000,unit:'m',strength:'hard',target:{place_id:'URA:AREA:CLEMENTI',name:'CLEMENTI'},distance_type:'straight_line',distance_preference:'bounded'},
  {criterion_id:'place_proximity:URA:AREA:CLEMENTI-preference',field:'place_proximity',operator:'lte',value:1000,unit:'m',strength:'soft',weight:1,target:{place_id:'URA:AREA:CLEMENTI',name:'CLEMENTI'},distance_type:'straight_line',distance_preference:'nearer'},
  {criterion_id:'place_proximity:PublicTransport-preference',field:'place_proximity',operator:'lte',value:800,unit:'m',strength:'soft',weight:1,target:{category:'PublicTransport',name:"Nearest MRT or bus stop"},distance_type:'straight_line',distance_preference:'bounded'},
]};
export function attribute(field:string,operator:ModelIntake['upsert'][number]['operator'],value:ModelIntake['upsert'][number]['value'],quote:string,options:Partial<ModelIntake['upsert'][number]>={}):ModelIntake['upsert'][number]{
  const units:Record<string,ModelIntake['upsert'][number]['unit']>={price:'SGD/month',bedrooms:'count',bathrooms:'count',floor_area_sqft:'sqft',land_area_sqft:'sqft',build_year:'year',lease_months:'month',commute_time:'min',mrt_distance_m:'m',mrt_walking_mins:'min'};
  return {field,operator,value,unit:units[field]??null,strength:'hard',weight:null,target_name:null,transport_mode:null,quote,...options};
}
export function location(name:string|null,categories:ModelIntake['locations'][number]['categories'],quote:string,options:Partial<ModelIntake['locations'][number]>={}):ModelIntake['locations'][number]{return {name,categories,relation:'any_of',operator:'near',value:null,unit:null,strength:'soft',weight:null,distance_type:'straight_line',explicit_station:false,prefer_closer:!!name&&!['gte','between'].includes(options.operator||'near'),quote,...options};}
export function output(query:string,patch:Partial<ModelIntake>={}):ModelIntake{return {schema_version:'2.0',supported:true,upsert:[],locations:[],removals:[],clarifications:[],coverage:[{quote:query,disposition:'extracted'}],...patch};}
const ex=(id:string,query:string,patch:Partial<ModelIntake>,base=empty):IntakeExample=>({id,query,base,output:output(query,patch)});
export const INTAKE_EXAMPLES:IntakeExample[]=[
  {
    "id": "short-replace-neighbourhood",
    "query": "Instead, find me a home near Holland Village.",
    "base": {
      "listing_type": "RENT",
      "criteria": [
        {
          "criterion_id": "minimum-bedrooms",
          "field": "bedrooms",
          "operator": "eq",
          "value": 1,
          "unit": "count",
          "strength": "hard"
        },
        {
          "criterion_id": "monthly-rent",
          "field": "price",
          "operator": "lte",
          "value": 3000,
          "unit": "SGD/month",
          "strength": "hard"
        },
        {
          "criterion_id": "place_proximity:LTA:MRT:CHINATOWN-preference",
          "field": "place_proximity",
          "operator": "lte",
          "value": 1000,
          "unit": "m",
          "strength": "soft",
          "weight": 1,
          "target": {
            "place_id": "LTA:MRT:CHINATOWN",
            "name": "CHINATOWN MRT STATION"
          },
          "distance_type": "straight_line",
          "distance_preference": "nearer"
        },
        {
          "criterion_id": "transport-preference",
          "field": "place_proximity",
          "operator": "lte",
          "value": 800,
          "unit": "m",
          "strength": "soft",
          "weight": 1,
          "target": {
            "category": "PublicTransport",
            "name": "Nearest MRT or bus stop"
          },
          "distance_type": "straight_line",
          "distance_preference": "bounded"
        }
      ]
    },
    "output": {
      "schema_version": "2.0",
      "supported": true,
      "upsert": [],
      "locations": [
        {
          "name": "Holland Village",
          "categories": [],
          "relation": "any_of",
          "operator": "near",
          "value": null,
          "unit": null,
          "strength": "soft",
          "weight": null,
          "distance_type": "straight_line",
          "explicit_station": false,
          "prefer_closer": true,
          "quote": "Instead, find me a home near Holland Village."
        }
      ],
      "removals": [
        {
          "field": "place_proximity",
          "criterion_id": "place_proximity:LTA:MRT:CHINATOWN-preference",
          "quote": "Instead, find me a home near Holland Village."
        }
      ],
      "clarifications": [],
      "coverage": [
        {
          "quote": "Instead, find me a home near Holland Village.",
          "disposition": "extracted"
        }
      ]
    }
  },
  {
    "id": "chinese-neighbourhood",
    "query": "Find me a home near Chinatown for SGD 3000 per month with exactly one bedroom.",
    "base": {
      "listing_type": "RENT",
      "criteria": []
    },
    "output": {
      "schema_version": "2.0",
      "supported": true,
      "upsert": [
        {
          "field": "price",
          "operator": "lte",
          "value": 3000,
          "unit": "SGD/month",
          "strength": "hard",
          "weight": null,
          "target_name": null,
          "transport_mode": null,
          "quote": "Find me a home near Chinatown for SGD 3000 per month with exactly one bedroom."
        },
        {
          "field": "bedrooms",
          "operator": "eq",
          "value": 1,
          "unit": "count",
          "strength": "hard",
          "weight": null,
          "target_name": null,
          "transport_mode": null,
          "quote": "Find me a home near Chinatown for SGD 3000 per month with exactly one bedroom."
        }
      ],
      "locations": [
        {
          "name": "Chinatown",
          "categories": [],
          "relation": "any_of",
          "operator": "near",
          "value": null,
          "unit": null,
          "strength": "soft",
          "weight": null,
          "distance_type": "straight_line",
          "explicit_station": false,
          "prefer_closer": true,
          "quote": "Find me a home near Chinatown for SGD 3000 per month with exactly one bedroom."
        }
      ],
      "removals": [],
      "clarifications": [],
      "coverage": [
        {
          "quote": "Find me a home near Chinatown for SGD 3000 per month with exactly one bedroom.",
          "disposition": "extracted"
        }
      ]
    }
  },
  {
    "id": "rent-ceiling",
    "query": "I can pay at most two thousand Singapore dollars each month, no more.",
    "base": {
      "listing_type": "RENT",
      "criteria": []
    },
    "output": {
      "schema_version": "2.0",
      "supported": true,
      "upsert": [
        {
          "field": "price",
          "operator": "lte",
          "value": 2000,
          "unit": "SGD/month",
          "strength": "hard",
          "weight": null,
          "target_name": null,
          "transport_mode": null,
          "quote": "I can pay at most two thousand Singapore dollars each month, no more."
        }
      ],
      "locations": [],
      "removals": [],
      "clarifications": [],
      "coverage": [
        {
          "quote": "I can pay at most two thousand Singapore dollars each month, no more.",
          "disposition": "extracted"
        }
      ]
    }
  },
  {
    "id": "rent-interval",
    "query": "I would prefer monthly rent between SGD 1800 and 2400.",
    "base": {
      "listing_type": "RENT",
      "criteria": []
    },
    "output": {
      "schema_version": "2.0",
      "supported": true,
      "upsert": [
        {
          "field": "price",
          "operator": "between",
          "value": [
            1800,
            2400
          ],
          "unit": "SGD/month",
          "strength": "hard",
          "weight": null,
          "target_name": null,
          "transport_mode": null,
          "quote": "I would prefer monthly rent between SGD 1800 and 2400."
        }
      ],
      "locations": [],
      "removals": [],
      "clarifications": [],
      "coverage": [
        {
          "quote": "I would prefer monthly rent between SGD 1800 and 2400.",
          "disposition": "extracted"
        }
      ]
    }
  },
  {
    "id": "rent-soft",
    "query": "SGD 3000 is the maximum; below 2500 would be even better.",
    "base": {
      "listing_type": "RENT",
      "criteria": []
    },
    "output": {
      "schema_version": "2.0",
      "supported": true,
      "upsert": [
        {
          "field": "price",
          "operator": "lte",
          "value": 3000,
          "unit": "SGD/month",
          "strength": "hard",
          "weight": null,
          "target_name": null,
          "transport_mode": null,
          "quote": "SGD 3000 is the maximum; below 2500 would be even better."
        },
        {
          "field": "price",
          "operator": "lte",
          "value": 2500,
          "unit": "SGD/month",
          "strength": "soft",
          "weight": null,
          "target_name": null,
          "transport_mode": null,
          "quote": "SGD 3000 is the maximum; below 2500 would be even better."
        }
      ],
      "locations": [],
      "removals": [],
      "clarifications": [],
      "coverage": [
        {
          "quote": "SGD 3000 is the maximum; below 2500 would be even better.",
          "disposition": "extracted"
        }
      ]
    }
  },
  {
    "id": "exact-layout",
    "query": "I want one bedroom and one bathroom, just 1B1B.",
    "base": {
      "listing_type": "RENT",
      "criteria": []
    },
    "output": {
      "schema_version": "2.0",
      "supported": true,
      "upsert": [
        {
          "field": "bedrooms",
          "operator": "eq",
          "value": 1,
          "unit": "count",
          "strength": "hard",
          "weight": null,
          "target_name": null,
          "transport_mode": null,
          "quote": "I want one bedroom and one bathroom, just 1B1B."
        },
        {
          "field": "bathrooms",
          "operator": "eq",
          "value": 1,
          "unit": "count",
          "strength": "hard",
          "weight": null,
          "target_name": null,
          "transport_mode": null,
          "quote": "I want one bedroom and one bathroom, just 1B1B."
        }
      ],
      "locations": [],
      "removals": [],
      "clarifications": [],
      "coverage": [
        {
          "quote": "I want one bedroom and one bathroom, just 1B1B.",
          "disposition": "extracted"
        }
      ]
    }
  },
  {
    "id": "bedroom-minimum",
    "query": "At least two bedrooms, three is fine too, and at least two bathrooms.",
    "base": {
      "listing_type": "RENT",
      "criteria": []
    },
    "output": {
      "schema_version": "2.0",
      "supported": true,
      "upsert": [
        {
          "field": "bedrooms",
          "operator": "gte",
          "value": 2,
          "unit": "count",
          "strength": "hard",
          "weight": null,
          "target_name": null,
          "transport_mode": null,
          "quote": "At least two bedrooms, three is fine too, and at least two bathrooms."
        },
        {
          "field": "bathrooms",
          "operator": "gte",
          "value": 2,
          "unit": "count",
          "strength": "hard",
          "weight": null,
          "target_name": null,
          "transport_mode": null,
          "quote": "At least two bedrooms, three is fine too, and at least two bathrooms."
        }
      ],
      "locations": [],
      "removals": [],
      "clarifications": [],
      "coverage": [
        {
          "quote": "At least two bedrooms, three is fine too, and at least two bathrooms.",
          "disposition": "extracted"
        }
      ]
    }
  },
  {
    "id": "ambiguous-bedroom",
    "query": "Find me a two-room place.",
    "base": {
      "listing_type": "RENT",
      "criteria": []
    },
    "output": {
      "schema_version": "2.0",
      "supported": true,
      "upsert": [],
      "locations": [],
      "removals": [],
      "clarifications": [
        {
          "quote": "Find me a two-room place.",
          "question": "Do you need at least two bedrooms or exactly two?"
        }
      ],
      "coverage": [
        {
          "quote": "Find me a two-room place.",
          "disposition": "clarification"
        }
      ]
    }
  },
  {
    "id": "hdb-model",
    "query": "Find an HDB 3-room flat.",
    "base": {
      "listing_type": "RENT",
      "criteria": []
    },
    "output": {
      "schema_version": "2.0",
      "supported": true,
      "upsert": [
        {
          "field": "property_type",
          "operator": "eq",
          "value": "HDB Flat",
          "unit": null,
          "strength": "hard",
          "weight": null,
          "target_name": null,
          "transport_mode": null,
          "quote": "HDB"
        }
      ],
      "locations": [],
      "removals": [],
      "clarifications": [
        {
          "quote": "3-room flat",
          "question": "An HDB 3-room model does not mean three bedrooms. How many bedrooms do you need?"
        }
      ],
      "coverage": [
        {
          "quote": "Find an HDB 3-room flat.",
          "disposition": "extracted"
        }
      ]
    }
  },
  {
    "id": "area-original-unit",
    "query": "The home must be at least forty-five square metres and no more than 70 square metres.",
    "base": {
      "listing_type": "RENT",
      "criteria": []
    },
    "output": {
      "schema_version": "2.0",
      "supported": true,
      "upsert": [
        {
          "field": "floor_area_sqft",
          "operator": "between",
          "value": [
            45,
            70
          ],
          "unit": "sqm",
          "strength": "hard",
          "weight": null,
          "target_name": null,
          "transport_mode": null,
          "quote": "The home must be at least forty-five square metres and no more than 70 square metres."
        }
      ],
      "locations": [],
      "removals": [],
      "clarifications": [],
      "coverage": [
        {
          "quote": "The home must be at least forty-five square metres and no more than 70 square metres.",
          "disposition": "extracted"
        }
      ]
    }
  },
  {
    "id": "area-imperial",
    "query": "500 sqft is the minimum; 700 sqft would be preferable.",
    "base": {
      "listing_type": "RENT",
      "criteria": []
    },
    "output": {
      "schema_version": "2.0",
      "supported": true,
      "upsert": [
        {
          "field": "floor_area_sqft",
          "operator": "gte",
          "value": 500,
          "unit": "sqft",
          "strength": "hard",
          "weight": null,
          "target_name": null,
          "transport_mode": null,
          "quote": "500 sqft is the minimum; 700 sqft would be preferable."
        },
        {
          "field": "floor_area_sqft",
          "operator": "gte",
          "value": 700,
          "unit": "sqft",
          "strength": "soft",
          "weight": null,
          "target_name": null,
          "transport_mode": null,
          "quote": "500 sqft is the minimum; 700 sqft would be preferable."
        }
      ],
      "locations": [],
      "removals": [],
      "clarifications": [],
      "coverage": [
        {
          "quote": "500 sqft is the minimum; 700 sqft would be preferable.",
          "disposition": "extracted"
        }
      ]
    }
  },
  {
    "id": "private-bath",
    "query": "I do not want to share bathing and toilet facilities with other people.",
    "base": {
      "listing_type": "RENT",
      "criteria": []
    },
    "output": {
      "schema_version": "2.0",
      "supported": true,
      "upsert": [
        {
          "field": "amenities",
          "operator": "eq",
          "value": "private_bathroom",
          "unit": null,
          "strength": "hard",
          "weight": null,
          "target_name": null,
          "transport_mode": null,
          "quote": "I do not want to share bathing and toilet facilities with other people."
        }
      ],
      "locations": [],
      "removals": [],
      "clarifications": [],
      "coverage": [
        {
          "quote": "I do not want to share bathing and toilet facilities with other people.",
          "disposition": "extracted"
        }
      ]
    }
  },
  {
    "id": "bath-count-is-not-private",
    "query": "One bathroom is enough; a private bathroom is not required.",
    "base": {
      "listing_type": "RENT",
      "criteria": [
        {
          "criterion_id": "amenities:private_bathroom",
          "field": "amenities",
          "operator": "eq",
          "value": "private_bathroom",
          "strength": "hard"
        }
      ]
    },
    "output": {
      "schema_version": "2.0",
      "supported": true,
      "upsert": [
        {
          "field": "bathrooms",
          "operator": "eq",
          "value": 1,
          "unit": "count",
          "strength": "hard",
          "weight": null,
          "target_name": null,
          "transport_mode": null,
          "quote": "One bathroom is enough; a private bathroom is not required."
        }
      ],
      "locations": [],
      "removals": [
        {
          "field": "amenities",
          "criterion_id": "amenities:private_bathroom",
          "quote": "One bathroom is enough; a private bathroom is not required."
        }
      ],
      "clarifications": [],
      "coverage": [
        {
          "quote": "One bathroom is enough; a private bathroom is not required.",
          "disposition": "extracted"
        }
      ]
    }
  },
  {
    "id": "room-no-landlord",
    "query": "Find a common room where cooking is allowed, without a live-in landlord.",
    "base": {
      "listing_type": "RENT",
      "criteria": []
    },
    "output": {
      "schema_version": "2.0",
      "supported": true,
      "upsert": [
        {
          "field": "rental_scope",
          "operator": "eq",
          "value": "room",
          "unit": null,
          "strength": "hard",
          "weight": null,
          "target_name": null,
          "transport_mode": null,
          "quote": "Find a common room where cooking is allowed, without a live-in landlord."
        },
        {
          "field": "amenities",
          "operator": "eq",
          "value": "common_room",
          "unit": null,
          "strength": "hard",
          "weight": null,
          "target_name": null,
          "transport_mode": null,
          "quote": "Find a common room where cooking is allowed, without a live-in landlord."
        },
        {
          "field": "amenities",
          "operator": "eq",
          "value": "cooking_allowed",
          "unit": null,
          "strength": "hard",
          "weight": null,
          "target_name": null,
          "transport_mode": null,
          "quote": "Find a common room where cooking is allowed, without a live-in landlord."
        },
        {
          "field": "amenities",
          "operator": "eq",
          "value": "no_live_in_landlord",
          "unit": null,
          "strength": "hard",
          "weight": null,
          "target_name": null,
          "transport_mode": null,
          "quote": "Find a common room where cooking is allowed, without a live-in landlord."
        }
      ],
      "locations": [],
      "removals": [],
      "clarifications": [],
      "coverage": [
        {
          "quote": "Find a common room where cooking is allowed, without a live-in landlord.",
          "disposition": "extracted"
        }
      ]
    }
  },
  {
    "id": "amenity-preferences",
    "query": "Air conditioning is mandatory, being allowed a cat would be better, and I do not mind whether there is a pool.",
    "base": {
      "listing_type": "RENT",
      "criteria": []
    },
    "output": {
      "schema_version": "2.0",
      "supported": true,
      "upsert": [
        {
          "field": "amenities",
          "operator": "eq",
          "value": "air_conditioning",
          "unit": null,
          "strength": "hard",
          "weight": null,
          "target_name": null,
          "transport_mode": null,
          "quote": "Air conditioning is mandatory, being allowed a cat would be better, and I do not mind whether there is a pool."
        },
        {
          "field": "amenities",
          "operator": "eq",
          "value": "pet_policy",
          "unit": null,
          "strength": "soft",
          "weight": null,
          "target_name": null,
          "transport_mode": null,
          "quote": "Air conditioning is mandatory, being allowed a cat would be better, and I do not mind whether there is a pool."
        }
      ],
      "locations": [],
      "removals": [],
      "clarifications": [],
      "coverage": [
        {
          "quote": "Air conditioning is mandatory, being allowed a cat would be better, and I do not mind whether there is a pool.",
          "disposition": "extracted"
        }
      ]
    }
  },
  {
    "id": "furniture-lease",
    "query": "It must be fully furnished, the lease at least half a year, and move-in before 10 January next year.",
    "base": {
      "listing_type": "RENT",
      "criteria": []
    },
    "output": {
      "schema_version": "2.0",
      "supported": true,
      "upsert": [
        {
          "field": "furnishing",
          "operator": "eq",
          "value": "fully_furnished",
          "unit": null,
          "strength": "hard",
          "weight": null,
          "target_name": null,
          "transport_mode": null,
          "quote": "It must be fully furnished, the lease at least half a year, and move-in before 10 January next year."
        },
        {
          "field": "lease_months",
          "operator": "gte",
          "value": 6,
          "unit": "month",
          "strength": "hard",
          "weight": null,
          "target_name": null,
          "transport_mode": null,
          "quote": "It must be fully furnished, the lease at least half a year, and move-in before 10 January next year."
        },
        {
          "field": "move_in_date",
          "operator": "lte",
          "value": "2027-01-10",
          "unit": null,
          "strength": "hard",
          "weight": null,
          "target_name": null,
          "transport_mode": null,
          "quote": "It must be fully furnished, the lease at least half a year, and move-in before 10 January next year."
        }
      ],
      "locations": [],
      "removals": [],
      "clarifications": [],
      "coverage": [
        {
          "quote": "It must be fully furnished, the lease at least half a year, and move-in before 10 January next year.",
          "disposition": "extracted"
        }
      ]
    }
  },
  {
    "id": "property-district",
    "query": "Only Condominium homes in D05 or D21; CCR is a preference, not mandatory.",
    "base": {
      "listing_type": "RENT",
      "criteria": []
    },
    "output": {
      "schema_version": "2.0",
      "supported": true,
      "upsert": [
        {
          "field": "district",
          "operator": "in",
          "value": [
            "D05",
            "D21"
          ],
          "unit": null,
          "strength": "hard",
          "weight": null,
          "target_name": null,
          "transport_mode": null,
          "quote": "Only Condominium homes in D05 or D21; CCR is a preference, not mandatory."
        },
        {
          "field": "property_type",
          "operator": "eq",
          "value": "Condominium",
          "unit": null,
          "strength": "hard",
          "weight": null,
          "target_name": null,
          "transport_mode": null,
          "quote": "Only Condominium homes in D05 or D21; CCR is a preference, not mandatory."
        },
        {
          "field": "region_code",
          "operator": "eq",
          "value": "CCR",
          "unit": null,
          "strength": "soft",
          "weight": null,
          "target_name": null,
          "transport_mode": null,
          "quote": "Only Condominium homes in D05 or D21; CCR is a preference, not mandatory."
        }
      ],
      "locations": [],
      "removals": [],
      "clarifications": [],
      "coverage": [
        {
          "quote": "Only Condominium homes in D05 or D21; CCR is a preference, not mandatory.",
          "disposition": "extracted"
        }
      ]
    }
  },
  {
    "id": "building-land",
    "query": "At least 1000 square feet of land, built from 2000 onwards, with postal code 059817.",
    "base": {
      "listing_type": "RENT",
      "criteria": []
    },
    "output": {
      "schema_version": "2.0",
      "supported": true,
      "upsert": [
        {
          "field": "land_area_sqft",
          "operator": "gte",
          "value": 1000,
          "unit": "sqft",
          "strength": "hard",
          "weight": null,
          "target_name": null,
          "transport_mode": null,
          "quote": "At least 1000 square feet of land, built from 2000 onwards, with postal code 059817."
        },
        {
          "field": "build_year",
          "operator": "gte",
          "value": 2000,
          "unit": "year",
          "strength": "hard",
          "weight": null,
          "target_name": null,
          "transport_mode": null,
          "quote": "At least 1000 square feet of land, built from 2000 onwards, with postal code 059817."
        },
        {
          "field": "postal_code",
          "operator": "eq",
          "value": "059817",
          "unit": null,
          "strength": "hard",
          "weight": null,
          "target_name": null,
          "transport_mode": null,
          "quote": "At least 1000 square feet of land, built from 2000 onwards, with postal code 059817."
        }
      ],
      "locations": [],
      "removals": [],
      "clarifications": [],
      "coverage": [
        {
          "quote": "At least 1000 square feet of land, built from 2000 onwards, with postal code 059817.",
          "disposition": "extracted"
        }
      ]
    }
  },
  {
    "id": "university-geo",
    "query": "Budget 2000, within five kilometres of NTU; ideally an MRT station or bus stop within one kilometre of the home.",
    "base": {
      "listing_type": "RENT",
      "criteria": []
    },
    "output": {
      "schema_version": "2.0",
      "supported": true,
      "upsert": [
        {
          "field": "price",
          "operator": "lte",
          "value": 2000,
          "unit": "SGD/month",
          "strength": "hard",
          "weight": null,
          "target_name": null,
          "transport_mode": null,
          "quote": "Budget 2000, within five kilometres of NTU; ideally an MRT station or bus stop within one kilometre of the home."
        }
      ],
      "locations": [
        {
          "name": "Nanyang Technological University",
          "categories": [],
          "relation": "any_of",
          "operator": "lte",
          "value": 5,
          "unit": "km",
          "strength": "hard",
          "weight": null,
          "distance_type": "straight_line",
          "explicit_station": false,
          "prefer_closer": true,
          "quote": "Budget 2000, within five kilometres of NTU; ideally an MRT station or bus stop within one kilometre of the home."
        },
        {
          "name": null,
          "categories": [
            "MRTStation",
            "BusStop"
          ],
          "relation": "any_of",
          "operator": "lte",
          "value": 1,
          "unit": "km",
          "strength": "soft",
          "weight": null,
          "distance_type": "straight_line",
          "explicit_station": false,
          "prefer_closer": false,
          "quote": "Budget 2000, within five kilometres of NTU; ideally an MRT station or bus stop within one kilometre of the home."
        }
      ],
      "removals": [],
      "clarifications": [],
      "coverage": [
        {
          "quote": "Budget 2000, within five kilometres of NTU; ideally an MRT station or bus stop within one kilometre of the home.",
          "disposition": "extracted"
        }
      ]
    }
  },
  {
    "id": "transport-and",
    "query": "Both an MRT station and a bus stop must be within 800 metres.",
    "base": {
      "listing_type": "RENT",
      "criteria": []
    },
    "output": {
      "schema_version": "2.0",
      "supported": true,
      "upsert": [],
      "locations": [
        {
          "name": null,
          "categories": [
            "MRTStation",
            "BusStop"
          ],
          "relation": "all_of",
          "operator": "lte",
          "value": 800,
          "unit": "m",
          "strength": "hard",
          "weight": null,
          "distance_type": "straight_line",
          "explicit_station": false,
          "prefer_closer": false,
          "quote": "Both an MRT station and a bus stop must be within 800 metres."
        }
      ],
      "removals": [],
      "clarifications": [],
      "coverage": [
        {
          "quote": "Both an MRT station and a bus stop must be within 800 metres.",
          "disposition": "extracted"
        }
      ]
    }
  },
  {
    "id": "bus-only",
    "query": "A bus stop must be no more than 500 metres away; closer would be more convenient.",
    "base": {
      "listing_type": "RENT",
      "criteria": []
    },
    "output": {
      "schema_version": "2.0",
      "supported": true,
      "upsert": [],
      "locations": [
        {
          "name": null,
          "categories": [
            "BusStop"
          ],
          "relation": "any_of",
          "operator": "lte",
          "value": 500,
          "unit": "m",
          "strength": "hard",
          "weight": null,
          "distance_type": "straight_line",
          "explicit_station": false,
          "prefer_closer": false,
          "quote": "A bus stop must be no more than 500 metres away; closer would be more convenient."
        }
      ],
      "removals": [],
      "clarifications": [],
      "coverage": [
        {
          "quote": "A bus stop must be no more than 500 metres away; closer would be more convenient.",
          "disposition": "extracted"
        }
      ]
    }
  },
  {
    "id": "near-no-radius",
    "query": "Somewhere near Clementi is fine, and preferably close to a supermarket too.",
    "base": {
      "listing_type": "RENT",
      "criteria": []
    },
    "output": {
      "schema_version": "2.0",
      "supported": true,
      "upsert": [],
      "locations": [
        {
          "name": "Clementi",
          "categories": [],
          "relation": "any_of",
          "operator": "near",
          "value": null,
          "unit": null,
          "strength": "soft",
          "weight": null,
          "distance_type": "straight_line",
          "explicit_station": false,
          "prefer_closer": true,
          "quote": "Somewhere near Clementi is fine, and preferably close to a supermarket too."
        },
        {
          "name": null,
          "categories": [
            "Supermarket"
          ],
          "relation": "any_of",
          "operator": "near",
          "value": null,
          "unit": null,
          "strength": "soft",
          "weight": null,
          "distance_type": "straight_line",
          "explicit_station": false,
          "prefer_closer": false,
          "quote": "Somewhere near Clementi is fine, and preferably close to a supermarket too."
        }
      ],
      "removals": [],
      "clarifications": [],
      "coverage": [
        {
          "quote": "Somewhere near Clementi is fine, and preferably close to a supermarket too.",
          "disposition": "extracted"
        }
      ]
    }
  },
  {
    "id": "named-station",
    "query": "No more than one kilometre from Clementi MRT, measured by walking distance.",
    "base": {
      "listing_type": "RENT",
      "criteria": []
    },
    "output": {
      "schema_version": "2.0",
      "supported": true,
      "upsert": [],
      "locations": [
        {
          "name": "Clementi MRT",
          "categories": [],
          "relation": "any_of",
          "operator": "lte",
          "value": 1,
          "unit": "km",
          "strength": "hard",
          "weight": null,
          "distance_type": "walking",
          "explicit_station": true,
          "prefer_closer": true,
          "quote": "No more than one kilometre from Clementi MRT, measured by walking distance."
        }
      ],
      "removals": [],
      "clarifications": [],
      "coverage": [
        {
          "quote": "No more than one kilometre from Clementi MRT, measured by walking distance.",
          "disposition": "extracted"
        }
      ]
    }
  },
  {
    "id": "distance-interval",
    "query": "Not too close to NUS or too far away: between one and three kilometres.",
    "base": {
      "listing_type": "RENT",
      "criteria": []
    },
    "output": {
      "schema_version": "2.0",
      "supported": true,
      "upsert": [],
      "locations": [
        {
          "name": "NUS",
          "categories": [],
          "relation": "any_of",
          "operator": "between",
          "value": [
            1,
            3
          ],
          "unit": "km",
          "strength": "hard",
          "weight": null,
          "distance_type": "straight_line",
          "explicit_station": false,
          "prefer_closer": false,
          "quote": "Not too close to NUS or too far away: between one and three kilometres."
        }
      ],
      "removals": [],
      "clarifications": [],
      "coverage": [
        {
          "quote": "Not too close to NUS or too far away: between one and three kilometres.",
          "disposition": "extracted"
        }
      ]
    }
  },
  {
    "id": "route-not-proximity",
    "query": "No more than 30 minutes to NUS by public transport; the listing-reported walk to MRT must be at most 8 minutes.",
    "base": {
      "listing_type": "RENT",
      "criteria": []
    },
    "output": {
      "schema_version": "2.0",
      "supported": true,
      "upsert": [
        {
          "field": "commute_time",
          "operator": "lte",
          "value": 30,
          "unit": "min",
          "strength": "hard",
          "weight": null,
          "target_name": "NUS",
          "transport_mode": "transit",
          "quote": "No more than 30 minutes to NUS by public transport; the listing-reported walk to MRT must be at most 8 minutes."
        },
        {
          "field": "mrt_walking_mins",
          "operator": "lte",
          "value": 8,
          "unit": "min",
          "strength": "hard",
          "weight": null,
          "target_name": null,
          "transport_mode": null,
          "quote": "No more than 30 minutes to NUS by public transport; the listing-reported walk to MRT must be at most 8 minutes."
        }
      ],
      "locations": [],
      "removals": [],
      "clarifications": [],
      "coverage": [
        {
          "quote": "No more than 30 minutes to NUS by public transport; the listing-reported walk to MRT must be at most 8 minutes.",
          "disposition": "extracted"
        }
      ]
    }
  },
  {
    "id": "reported-distance",
    "query": "The platform-reported nearest-MRT distance must be no more than 600 metres, not a straight-line calculation.",
    "base": {
      "listing_type": "RENT",
      "criteria": []
    },
    "output": {
      "schema_version": "2.0",
      "supported": true,
      "upsert": [
        {
          "field": "mrt_distance_m",
          "operator": "lte",
          "value": 600,
          "unit": "m",
          "strength": "hard",
          "weight": null,
          "target_name": null,
          "transport_mode": null,
          "quote": "The platform-reported nearest-MRT distance must be no more than 600 metres, not a straight-line calculation."
        }
      ],
      "locations": [],
      "removals": [],
      "clarifications": [],
      "coverage": [
        {
          "quote": "The platform-reported nearest-MRT distance must be no more than 600 metres, not a straight-line calculation.",
          "disposition": "extracted"
        }
      ]
    }
  },
  {
    "id": "plain-mrt-distance",
    "query": "The maximum distance between the home and the nearest MRT station is 650 metres.",
    "base": {
      "listing_type": "RENT",
      "criteria": []
    },
    "output": {
      "schema_version": "2.0",
      "supported": true,
      "upsert": [],
      "locations": [
        {
          "name": null,
          "categories": [
            "MRTStation"
          ],
          "relation": "any_of",
          "operator": "lte",
          "value": 650,
          "unit": "m",
          "strength": "hard",
          "weight": null,
          "distance_type": "straight_line",
          "explicit_station": false,
          "prefer_closer": false,
          "quote": "The maximum distance between the home and the nearest MRT station is 650 metres."
        }
      ],
      "removals": [],
      "clarifications": [],
      "coverage": [
        {
          "quote": "The maximum distance between the home and the nearest MRT station is 650 metres.",
          "disposition": "extracted"
        }
      ]
    }
  },
  {
    "id": "change-single",
    "query": "Raise the budget to 2600, make the private bathroom nice to have, and keep everything else.",
    "base": {
      "listing_type": "RENT",
      "criteria": [
        {
          "criterion_id": "monthly-rent",
          "field": "price",
          "operator": "lte",
          "value": 2000,
          "unit": "SGD/month",
          "strength": "hard"
        },
        {
          "criterion_id": "amenities:private_bathroom",
          "field": "amenities",
          "operator": "eq",
          "value": "private_bathroom",
          "strength": "hard"
        },
        {
          "criterion_id": "minimum-bedrooms",
          "field": "bedrooms",
          "operator": "gte",
          "value": 2,
          "unit": "count",
          "strength": "hard"
        }
      ]
    },
    "output": {
      "schema_version": "2.0",
      "supported": true,
      "upsert": [
        {
          "field": "price",
          "operator": "lte",
          "value": 2600,
          "unit": "SGD/month",
          "strength": "hard",
          "weight": null,
          "target_name": null,
          "transport_mode": null,
          "quote": "Raise the budget to 2600, make the private bathroom nice to have, and keep everything else."
        },
        {
          "field": "amenities",
          "operator": "eq",
          "value": "private_bathroom",
          "unit": null,
          "strength": "soft",
          "weight": null,
          "target_name": null,
          "transport_mode": null,
          "quote": "Raise the budget to 2600, make the private bathroom nice to have, and keep everything else."
        }
      ],
      "locations": [],
      "removals": [
        {
          "field": "amenities",
          "criterion_id": "amenities:private_bathroom",
          "quote": "Raise the budget to 2600, make the private bathroom nice to have, and keep everything else."
        }
      ],
      "clarifications": [],
      "coverage": [
        {
          "quote": "Raise the budget to 2600, make the private bathroom nice to have, and keep everything else.",
          "disposition": "extracted"
        }
      ]
    }
  },
  {
    "id": "remove-only",
    "query": "I no longer require a private bathroom, but keeping a cat must still be allowed.",
    "base": {
      "listing_type": "RENT",
      "criteria": [
        {
          "criterion_id": "amenities:private_bathroom",
          "field": "amenities",
          "operator": "eq",
          "value": "private_bathroom",
          "strength": "hard"
        },
        {
          "criterion_id": "amenities:pet_policy",
          "field": "amenities",
          "operator": "eq",
          "value": "pet_policy",
          "strength": "hard"
        }
      ]
    },
    "output": {
      "schema_version": "2.0",
      "supported": true,
      "upsert": [
        {
          "field": "amenities",
          "operator": "eq",
          "value": "pet_policy",
          "unit": null,
          "strength": "hard",
          "weight": null,
          "target_name": null,
          "transport_mode": null,
          "quote": "I no longer require a private bathroom, but keeping a cat must still be allowed."
        }
      ],
      "locations": [],
      "removals": [
        {
          "field": "amenities",
          "criterion_id": "amenities:private_bathroom",
          "quote": "I no longer require a private bathroom, but keeping a cat must still be allowed."
        }
      ],
      "clarifications": [],
      "coverage": [
        {
          "quote": "I no longer require a private bathroom, but keeping a cat must still be allowed.",
          "disposition": "extracted"
        }
      ]
    }
  },
  {
    "id": "transport-mode-update",
    "query": "The 30 minutes I mentioned is by public transport, not driving.",
    "base": {
      "listing_type": "RENT",
      "criteria": [
        {
          "criterion_id": "commute_time",
          "field": "commute_time",
          "operator": "lte",
          "value": 30,
          "unit": "min",
          "strength": "hard",
          "target": {
            "name": "NUS"
          },
          "transport_mode": "unspecified"
        }
      ]
    },
    "output": {
      "schema_version": "2.0",
      "supported": true,
      "upsert": [
        {
          "field": "commute_time",
          "operator": "lte",
          "value": 30,
          "unit": "min",
          "strength": "hard",
          "weight": null,
          "target_name": "NUS",
          "transport_mode": "transit",
          "quote": "The 30 minutes I mentioned is by public transport, not driving."
        }
      ],
      "locations": [],
      "removals": [],
      "clarifications": [],
      "coverage": [
        {
          "quote": "The 30 minutes I mentioned is by public transport, not driving.",
          "disposition": "extracted"
        }
      ]
    }
  },
  {
    "id": "refresh-has-change",
    "query": "Recommend again, preferably with a park within 700 metres.",
    "base": {
      "listing_type": "RENT",
      "criteria": []
    },
    "output": {
      "schema_version": "2.0",
      "supported": true,
      "upsert": [],
      "locations": [
        {
          "name": null,
          "categories": [
            "Park"
          ],
          "relation": "any_of",
          "operator": "lte",
          "value": 700,
          "unit": "m",
          "strength": "soft",
          "weight": null,
          "distance_type": "straight_line",
          "explicit_station": false,
          "prefer_closer": false,
          "quote": "Recommend again, preferably with a park within 700 metres."
        }
      ],
      "removals": [],
      "clarifications": [],
      "coverage": [
        {
          "quote": "Recommend again, preferably with a park within 700 metres.",
          "disposition": "extracted"
        }
      ]
    }
  },
  {
    "id": "restate-geographic-update",
    "query": "Let me update the requirements: rent still at most SGD 2400, within 4 km of Clementi, and preferably an MRT or bus stop within 800 metres.",
    "base": {
      "listing_type": "RENT",
      "criteria": [
        {
          "criterion_id": "minimum-bedrooms",
          "field": "bedrooms",
          "operator": "eq",
          "value": 1,
          "unit": "count",
          "strength": "hard"
        },
        {
          "criterion_id": "bathrooms",
          "field": "bathrooms",
          "operator": "eq",
          "value": 1,
          "unit": "count",
          "strength": "hard"
        },
        {
          "criterion_id": "monthly-rent",
          "field": "price",
          "operator": "lte",
          "value": 2400,
          "unit": "SGD/month",
          "strength": "hard"
        },
        {
          "criterion_id": "place_proximity:URA:AREA:CLEMENTI",
          "field": "place_proximity",
          "operator": "lte",
          "value": 4000,
          "unit": "m",
          "strength": "hard",
          "target": {
            "place_id": "URA:AREA:CLEMENTI",
            "name": "CLEMENTI"
          },
          "distance_type": "straight_line",
          "distance_preference": "bounded"
        },
        {
          "criterion_id": "place_proximity:URA:AREA:CLEMENTI-preference",
          "field": "place_proximity",
          "operator": "lte",
          "value": 1000,
          "unit": "m",
          "strength": "soft",
          "weight": 1,
          "target": {
            "place_id": "URA:AREA:CLEMENTI",
            "name": "CLEMENTI"
          },
          "distance_type": "straight_line",
          "distance_preference": "nearer"
        },
        {
          "criterion_id": "place_proximity:PublicTransport-preference",
          "field": "place_proximity",
          "operator": "lte",
          "value": 800,
          "unit": "m",
          "strength": "soft",
          "weight": 1,
          "target": {
            "category": "PublicTransport",
            "name": "Nearest MRT or bus stop"
          },
          "distance_type": "straight_line",
          "distance_preference": "bounded"
        }
      ]
    },
    "output": {
      "schema_version": "2.0",
      "supported": true,
      "upsert": [
        {
          "field": "price",
          "operator": "lte",
          "value": 2400,
          "unit": "SGD/month",
          "strength": "hard",
          "weight": null,
          "target_name": null,
          "transport_mode": null,
          "quote": "Let me update the requirements: rent still at most SGD 2400, within 4 km of Clementi, and preferably an MRT or bus stop within 800 metres."
        }
      ],
      "locations": [
        {
          "name": "Clementi",
          "categories": [],
          "relation": "any_of",
          "operator": "lte",
          "value": 4,
          "unit": "km",
          "strength": "hard",
          "weight": null,
          "distance_type": "straight_line",
          "explicit_station": false,
          "prefer_closer": true,
          "quote": "Let me update the requirements: rent still at most SGD 2400, within 4 km of Clementi, and preferably an MRT or bus stop within 800 metres."
        },
        {
          "name": null,
          "categories": [
            "MRTStation",
            "BusStop"
          ],
          "relation": "any_of",
          "operator": "lte",
          "value": 800,
          "unit": "m",
          "strength": "soft",
          "weight": null,
          "distance_type": "straight_line",
          "explicit_station": false,
          "prefer_closer": false,
          "quote": "Let me update the requirements: rent still at most SGD 2400, within 4 km of Clementi, and preferably an MRT or bus stop within 800 metres."
        }
      ],
      "removals": [],
      "clarifications": [],
      "coverage": [
        {
          "quote": "Let me update the requirements: rent still at most SGD 2400, within 4 km of Clementi, and preferably an MRT or bus stop within 800 metres.",
          "disposition": "extracted"
        }
      ]
    }
  },
  {
    "id": "cancel-layout-only",
    "query": "No bedroom or bathroom count limits now; keep Clementi within 4 km, the 2400 budget and transport preferences.",
    "base": {
      "listing_type": "RENT",
      "criteria": [
        {
          "criterion_id": "minimum-bedrooms",
          "field": "bedrooms",
          "operator": "eq",
          "value": 1,
          "unit": "count",
          "strength": "hard"
        },
        {
          "criterion_id": "bathrooms",
          "field": "bathrooms",
          "operator": "eq",
          "value": 1,
          "unit": "count",
          "strength": "hard"
        },
        {
          "criterion_id": "monthly-rent",
          "field": "price",
          "operator": "lte",
          "value": 2400,
          "unit": "SGD/month",
          "strength": "hard"
        },
        {
          "criterion_id": "place_proximity:URA:AREA:CLEMENTI",
          "field": "place_proximity",
          "operator": "lte",
          "value": 4000,
          "unit": "m",
          "strength": "hard",
          "target": {
            "place_id": "URA:AREA:CLEMENTI",
            "name": "CLEMENTI"
          },
          "distance_type": "straight_line",
          "distance_preference": "bounded"
        },
        {
          "criterion_id": "place_proximity:URA:AREA:CLEMENTI-preference",
          "field": "place_proximity",
          "operator": "lte",
          "value": 1000,
          "unit": "m",
          "strength": "soft",
          "weight": 1,
          "target": {
            "place_id": "URA:AREA:CLEMENTI",
            "name": "CLEMENTI"
          },
          "distance_type": "straight_line",
          "distance_preference": "nearer"
        },
        {
          "criterion_id": "place_proximity:PublicTransport-preference",
          "field": "place_proximity",
          "operator": "lte",
          "value": 800,
          "unit": "m",
          "strength": "soft",
          "weight": 1,
          "target": {
            "category": "PublicTransport",
            "name": "Nearest MRT or bus stop"
          },
          "distance_type": "straight_line",
          "distance_preference": "bounded"
        }
      ]
    },
    "output": {
      "schema_version": "2.0",
      "supported": true,
      "upsert": [],
      "locations": [],
      "removals": [
        {
          "field": "bedrooms",
          "criterion_id": "minimum-bedrooms",
          "quote": "No bedroom or bathroom count limits now; keep Clementi within 4 km, the 2400 budget and transport preferences."
        },
        {
          "field": "bathrooms",
          "criterion_id": "bathrooms",
          "quote": "No bedroom or bathroom count limits now; keep Clementi within 4 km, the 2400 budget and transport preferences."
        }
      ],
      "clarifications": [],
      "coverage": [
        {
          "quote": "No bedroom or bathroom count limits now; keep Clementi within 4 km, the 2400 budget and transport preferences.",
          "disposition": "removed"
        },
        {
          "quote": "No bedroom or bathroom count limits now; keep Clementi within 4 km, the 2400 budget and transport preferences.",
          "disposition": "context"
        }
      ]
    }
  },
  {
    "id": "replace-location-only",
    "query": "Not Clementi anymore; change to within 3 km of Jurong East and keep everything else.",
    "base": {
      "listing_type": "RENT",
      "criteria": [
        {
          "criterion_id": "minimum-bedrooms",
          "field": "bedrooms",
          "operator": "eq",
          "value": 1,
          "unit": "count",
          "strength": "hard"
        },
        {
          "criterion_id": "bathrooms",
          "field": "bathrooms",
          "operator": "eq",
          "value": 1,
          "unit": "count",
          "strength": "hard"
        },
        {
          "criterion_id": "monthly-rent",
          "field": "price",
          "operator": "lte",
          "value": 2400,
          "unit": "SGD/month",
          "strength": "hard"
        },
        {
          "criterion_id": "place_proximity:URA:AREA:CLEMENTI",
          "field": "place_proximity",
          "operator": "lte",
          "value": 4000,
          "unit": "m",
          "strength": "hard",
          "target": {
            "place_id": "URA:AREA:CLEMENTI",
            "name": "CLEMENTI"
          },
          "distance_type": "straight_line",
          "distance_preference": "bounded"
        },
        {
          "criterion_id": "place_proximity:URA:AREA:CLEMENTI-preference",
          "field": "place_proximity",
          "operator": "lte",
          "value": 1000,
          "unit": "m",
          "strength": "soft",
          "weight": 1,
          "target": {
            "place_id": "URA:AREA:CLEMENTI",
            "name": "CLEMENTI"
          },
          "distance_type": "straight_line",
          "distance_preference": "nearer"
        },
        {
          "criterion_id": "place_proximity:PublicTransport-preference",
          "field": "place_proximity",
          "operator": "lte",
          "value": 800,
          "unit": "m",
          "strength": "soft",
          "weight": 1,
          "target": {
            "category": "PublicTransport",
            "name": "Nearest MRT or bus stop"
          },
          "distance_type": "straight_line",
          "distance_preference": "bounded"
        }
      ]
    },
    "output": {
      "schema_version": "2.0",
      "supported": true,
      "upsert": [],
      "locations": [
        {
          "name": "Jurong East",
          "categories": [],
          "relation": "any_of",
          "operator": "lte",
          "value": 3,
          "unit": "km",
          "strength": "hard",
          "weight": null,
          "distance_type": "straight_line",
          "explicit_station": false,
          "prefer_closer": true,
          "quote": "Not Clementi anymore; change to within 3 km of Jurong East and keep everything else."
        }
      ],
      "removals": [
        {
          "field": "place_proximity",
          "criterion_id": "place_proximity:URA:AREA:CLEMENTI",
          "quote": "Not Clementi anymore; change to within 3 km of Jurong East and keep everything else."
        },
        {
          "field": "place_proximity",
          "criterion_id": "place_proximity:URA:AREA:CLEMENTI-preference",
          "quote": "Not Clementi anymore; change to within 3 km of Jurong East and keep everything else."
        }
      ],
      "clarifications": [],
      "coverage": [
        {
          "quote": "Not Clementi anymore; change to within 3 km of Jurong East and keep everything else.",
          "disposition": "extracted"
        }
      ]
    }
  },
  {
    "id": "unsupported-buy",
    "query": "I want to buy a home.",
    "base": {
      "listing_type": "RENT",
      "criteria": []
    },
    "output": {
      "schema_version": "2.0",
      "supported": false,
      "upsert": [],
      "locations": [],
      "removals": [],
      "clarifications": [
        {
          "quote": "I want to buy a home.",
          "question": "This system supports residential rentals. Would you like to look for a rental instead?"
        }
      ],
      "coverage": [
        {
          "quote": "I want to buy a home.",
          "disposition": "clarification"
        }
      ]
    }
  },
  {
    "id": "availability",
    "query": "It must still be available now; do not give me homes already rented out.",
    "base": {
      "listing_type": "RENT",
      "criteria": []
    },
    "output": {
      "schema_version": "2.0",
      "supported": true,
      "upsert": [
        {
          "field": "current_availability",
          "operator": "eq",
          "value": true,
          "unit": null,
          "strength": "hard",
          "weight": null,
          "target_name": null,
          "transport_mode": null,
          "quote": "It must still be available now; do not give me homes already rented out."
        }
      ],
      "locations": [],
      "removals": [],
      "clarifications": [],
      "coverage": [
        {
          "quote": "It must still be available now; do not give me homes already rented out.",
          "disposition": "extracted"
        }
      ]
    }
  },
  {
    "id": "market-region",
    "query": "Choose the OCR market region, without limiting the district.",
    "base": {
      "listing_type": "RENT",
      "criteria": []
    },
    "output": {
      "schema_version": "2.0",
      "supported": true,
      "upsert": [
        {
          "field": "region_code",
          "operator": "eq",
          "value": "OCR",
          "unit": null,
          "strength": "hard",
          "weight": null,
          "target_name": null,
          "transport_mode": null,
          "quote": "Choose the OCR market region, without limiting the district."
        }
      ],
      "locations": [],
      "removals": [],
      "clarifications": [],
      "coverage": [
        {
          "quote": "Choose the OCR market region, without limiting the district.",
          "disposition": "extracted"
        }
      ]
    }
  },
  {
    "id": "market-region-restrict-only",
    "query": "Only the RCR market region, no other market regions.",
    "base": {
      "listing_type": "RENT",
      "criteria": []
    },
    "output": {
      "schema_version": "2.0",
      "supported": true,
      "upsert": [
        {
          "field": "region_code",
          "operator": "eq",
          "value": "RCR",
          "unit": null,
          "strength": "hard",
          "weight": null,
          "target_name": null,
          "transport_mode": null,
          "quote": "Only the RCR market region, no other market regions."
        }
      ],
      "locations": [],
      "removals": [],
      "clarifications": [],
      "coverage": [
        {
          "quote": "Only the RCR market region, no other market regions.",
          "disposition": "extracted"
        }
      ]
    }
  },
  {
    "id": "two-digit-district",
    "query": "Only Singapore districts 08 or 19; ignore other districts for now.",
    "base": {
      "listing_type": "RENT",
      "criteria": []
    },
    "output": {
      "schema_version": "2.0",
      "supported": true,
      "upsert": [
        {
          "field": "district",
          "operator": "in",
          "value": [
            "D08",
            "D19"
          ],
          "unit": null,
          "strength": "hard",
          "weight": null,
          "target_name": null,
          "transport_mode": null,
          "quote": "Only Singapore districts 08 or 19; ignore other districts for now."
        }
      ],
      "locations": [],
      "removals": [],
      "clarifications": [],
      "coverage": [
        {
          "quote": "Only Singapore districts 08 or 19; ignore other districts for now.",
          "disposition": "extracted"
        }
      ]
    }
  }
];
// Example retrieval only; these tokens never create or override a user criterion.
function tokens(text:string){const ts=[...text.toLowerCase().matchAll(/[a-z]+|\d+/g)].map(x=>x[0]);for(const m of text.matchAll(/[\u4e00-\u9fff]+/g)){const chars=[...m[0]];ts.push(...chars);for(let i=0;i<chars.length-1;i++)ts.push(chars[i]+chars[i+1]);}return ts;}
export function selectIntakeExamples(query:string,count=2){const q=tokens(query),df=new Map<string,number>();for(const e of INTAKE_EXAMPLES)for(const t of new Set(tokens(e.query)))df.set(t,(df.get(t)||0)+1);
  return INTAKE_EXAMPLES.map((e,index)=>{const ts=tokens(e.query),score=q.reduce((n,t)=>{const tf=ts.filter(x=>x===t).length;return n+(tf?Math.log(1+(INTAKE_EXAMPLES.length-(df.get(t)||0)+.5)/((df.get(t)||0)+.5))*tf/(tf+1.2*(.25+.75*ts.length/20)):0);},0);return {e,index,score};}).sort((a,b)=>b.score-a.score||a.index-b.index).slice(0,count).map(x=>x.e);
}
