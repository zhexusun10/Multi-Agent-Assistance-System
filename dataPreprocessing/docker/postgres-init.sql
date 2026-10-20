-- Runs once on a fresh container volume. Trust auth is set by compose for
-- loopback development only; production must use real roles and passwords.
CREATE ROLE propertyguru_scraper LOGIN;
CREATE ROLE multi_agent_assistance LOGIN;
CREATE DATABASE propertyguru OWNER propertyguru_scraper;
CREATE DATABASE propertyguru_test OWNER propertyguru_scraper;
CREATE DATABASE multi_agent_assistance OWNER multi_agent_assistance;
CREATE DATABASE multi_agent_assistance_test OWNER multi_agent_assistance;
