CREATE TABLE `acl_groups` (
  `group_name` varchar(256) COLLATE utf8mb4_unicode_ci NOT NULL,
  `who` varchar(256) COLLATE utf8mb4_unicode_ci NOT NULL,
  PRIMARY KEY (`group_name`,`who`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE `acls` (
  `who` varchar(256) COLLATE utf8mb4_unicode_ci NOT NULL,
  `command` varchar(256) COLLATE utf8mb4_unicode_ci NOT NULL,
  PRIMARY KEY (`command`,`who`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

INSERT INTO `acls` VALUES ('sysops','addacl'),('sysops','delacl'),('sysops','groupadd'),('sysops','groupdel'),('users','help'),('sysops','listacls'),('sysops','meet');

CREATE TABLE `aliasses` (
  `command` varchar(256) COLLATE utf8mb4_unicode_ci NOT NULL,
  `is_command` tinyint(1) DEFAULT 0,
  `replacement_text` TEXT COLLATE utf8mb4_unicode_ci NOT NULL,
  `nr` int(12) NOT NULL AUTO_INCREMENT,
  PRIMARY KEY (`nr`),
  KEY `command` (`command`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
