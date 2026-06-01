-- Rename legacy shop key ``main`` → canonical ``inkelligentsticker``.
-- Safe to run multiple times (only touches rows still on ``main``).

UPDATE tkshop_products
   SET shop = 'inkelligentsticker'
 WHERE shop = 'main';
